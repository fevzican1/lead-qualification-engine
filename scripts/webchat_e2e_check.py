

"""Uctan uca webchat dogrulamasi — musteri akisi (stdlib-only WebSocket).

Gercek musteri yolunu taklit eder:
  1) GET  /health            -> kapi sagligi + gecikme
  2) GET  /chat              -> HTML sayfasi
  3) POST /api/session       -> sid + karsilama (ilk mesaj ROI karti)
  4) WS   /ws/{sid}          -> ready, mesaj gonder, yanit al (gecikme olcumu)
  5) WS   /ws/{sid} tekrar   -> oturum gecmisi korunuyor mu (reconnect)

Kullanim:  python scripts/webchat_e2e_check.py [base_url]
"""
from __future__ import annotations

import base64
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("WEBCHAT_URL") or "http://141.253.144.36:8765/").rstrip("/")
TIMEOUT = float(os.getenv("WEBCHAT_E2E_TIMEOUT", "30") or 30)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'GECTI' if ok else 'KALDI'} | {name} | {detail}", flush=True)

def _req(path: str, data: bytes | None = None) -> tuple[int, bytes, float]:
    req = urllib.request.Request(f"{BASE}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - t0


class WS:
    """Kucuk stdlib WebSocket istemcisi (text frame + okuma)."""

    def __init__(self, path: str):
        host = BASE.split("//", 1)[1]
        h, _, port = host.partition(":")
        self.sock = socket.create_connection((h, int(port or 80)), timeout=TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("handshake closed")
            self.buf += chunk
        head, _, rest = self.buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake: " + head.split(b"\r\n")[0].decode(errors="replace"))
        self.buf = rest
        # handshake sonrasi okuyucu: once buf'taki ilk frame'leri, sonra
        # socket'i tuket (tek okuyucu; ready yutulmaz, yaris yok).
        self.q = queue.Queue()
        self._reader_thread = None
        self._sock_lock = threading.Lock()
        self.start_keepalive()

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode())

    def _fill(self, n: int) -> bytes:
        lock = self._sock_lock
        while True:
            with lock:
                if len(self.buf) >= n:
                    out, self.buf = self.buf[:n], self.buf[n:]
                    return out
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        """Maskeli frame gonder (istemci -> sunucu yonu mask ZORUNLU)."""
        mask = os.urandom(4)
        n = len(payload)
        b1 = 0x80 | opcode  # FIN + opcode
        if n < 126:
            hdr = struct.pack("!BB", b1, 0x80 | n)
        elif n < (1 << 16):
            hdr = struct.pack("!BBH", b1, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", b1, 0x80 | 127, n)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def ping(self) -> None:
        # handshake'ten hemen sonra reader henuz ayakta degilken ayni segmentte
        # gelen greeting/ready'nin yutulmamasi icin once buf bosaltilir.
        try:
            keep = self.sock.gettimeout()
        except Exception:
            keep = None
        self.sock.settimeout(0.05)
        try:
            chunk = self.sock.recv(65536)
            if chunk:
                self.buf += chunk
        except (socket.timeout, BlockingIOError, OSError):
            pass
        finally:
            try:
                self.sock.settimeout(keep)
            except Exception:
                pass
        # handshake sonrasi reader thread'i baslat (PONG + kuyruk)
        self.q = queue.Queue()
        self._reader_thread = None
        self.start_keepalive()

    def start_keepalive(self, timeout: float = 5.0) -> None:
        """TEK reader thread baslat: frame okur + PONG verir + kuyruga koyar.

        uvicorn 20 sn'de bir PING atar; istemci yanitlamazsa 20 sn sonra
        baglantiyi 1011 'keepalive ping timeout' ile KOPARIR. LLM yaniti
        90 sn surebildigi icin bu, musterinin yazdigini gonderip cevap
        almadan once oturumun dusmesine yol aciyordu (donma/kopma).
        """
        if getattr(self, "_reader_thread", None) is not None:
            return
        self._sock_lock = threading.Lock()
        import threading as _th
        self._reader_thread = _th.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def _push_frames(self, data: bytes) -> None:
        """Ham baytlari cerceve(ler)e bolup kuyruga yazar (handshake artigi icin)."""
        i = 0
        n = len(data)
        while i + 2 <= n:
            b1, b2 = data[i], data[i + 1]
            opcode, ln = b1 & 0x0F, b2 & 0x7F
            i += 2
            if ln == 126:
                if i + 2 > n:
                    return
                ln = struct.unpack("!H", data[i:i + 2])[0]
                i += 2
            elif ln == 127:
                if i + 8 > n:
                    return
                ln = struct.unpack("!Q", data[i:i + 8])[0]
                i += 8
            if i + ln > n:
                # eksik veri: tamponun icinde kalsin, reader tamamlar
                with self._sock_lock:
                    self.buf = data[i - 2:] + self.buf
                return
            payload = data[i:i + ln]
            i += ln
            if opcode in (0x1, 0x2, 0x8):
                self.q.put((opcode, payload))

    def _reader(self) -> None:
        """TEK okuyucu: butun frame'leri okur, PONG'a cevap verir, kuyruga koyar.

        Neden ayri surucu yok: onceki surude keepalive thread'i ile ana
        recv() ayni socket'te yarisiyordu; keepalive metin frame'ini
        yutuyor/geri koyuyordu -> "ready" mesaji kaciyordu (ready=None).
        Tek okuyucu + kuyruk yaris ve kacirmayi tamamen ortadan kaldirir.
        """
        while True:
            try:
                b1, b2 = struct.unpack("!BB", self._fill(2))
                opcode, ln = b1 & 0x0F, b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack("!H", self._fill(2))[0]
                elif ln == 127:
                    ln = struct.unpack("!Q", self._fill(8))[0]
                data = self._fill(ln) if ln else b""
            except (RuntimeError, OSError):
                break
            if opcode == 0x9:  # PING -> PONG (donma/kopma sebebiydi)
                try:
                    self._send_frame(0xA, data)
                except OSError:
                    break
                continue
            if opcode == 0xA:
                continue
            self.q.put((opcode, data))
        self.q.put(None)

    def drain(self, want: str, timeout: float = TIMEOUT) -> dict | None:
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                item = self.q.get(timeout=min(0.5, left))
            except queue.Empty:
                continue
            if item is None:
                raise RuntimeError("closed by server (reader EOF)")
            opcode, data = item
            if opcode == 0x8:
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
                reason = data[2:].decode("utf-8", "replace") if len(data) > 2 else ""
                raise RuntimeError(f"closed by server (code={code} reason={reason!r})")
            if opcode not in (0x1, 0x2):
                continue
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                msg = {"type": "text", "text": data.decode("utf-8", "replace")}
            if msg.get("type") == want or msg.get("kind") == want:
                return msg

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def main() -> int:
    print(f"[webchat e2e] {BASE}", flush=True)

    code, body, dt = _req("/health")
    ok = code == 200 and b'"ok":true' in body.replace(b" ", b"")
    record("1) /health 200", ok, f"{code} {dt * 1000:.0f}ms")
    if not ok:
        print("  -> health red, devam edilmiyor")
        return 1

    code, body, dt = _req("/chat")
    record("2) /chat HTML", code == 200 and b"<html" in body.lower(),
           f"{code} {len(body)}B {dt * 1000:.0f}ms")

    payload = json.dumps({
        "name": "E2E Test", "lang": "tr",
        "brief": "Web sitemizde mobil hiz skorumuz 41, mesai disi canli karsilama yok.",
        "domain": "https://e2e-probe.example.com",
        "form": {"email": "e2e@example.com"},
    }).encode()
    code, body, dt = _req("/api/session", payload)
    try:
        sess = json.loads(body)
    except Exception:
        sess = {}
    sid = str(sess.get("sid") or "")
    greet = str(sess.get("greeting") or "")
    ok = code == 200 and bool(sid) and len(greet) > 20
    record("3) POST /api/session", ok, f"{code} sid={sid} {dt * 1000:.0f}ms")
    record("3b) ilk mesaj karsilama",
           len(greet) > 20,
           greet[:90].replace("\n", " "))
    if not ok:
        return 1

    ws = None
    try:
        ws = WS(f"/ws/{sid}")
        ready = ws.drain("ready", 15)
        record("4a) WS handshake + ready", ready is not None, str(ready)[:80])
        t0 = time.monotonic()
        ws.send("Sitemizde mobil hiz skorumuz 41 ve mesai disi kimse cevap vermiyor, ne yapabilirsiniz?")
        reply = ws.drain("agent", 90)
        lat = time.monotonic() - t0
        txt = str((reply or {}).get("text") or "")
        record("4b) WS yanit alindi", bool(txt), f"{lat:.2f}s | {txt[:80]}")
        record("4c) yanit gecikmesi < 60s", lat < 60, f"{lat:.2f}s")
    except Exception as exc:  # noqa: BLE001
        record("4) WS akisi", False, repr(exc))
    finally:
        if ws is not None:
            ws.close()

    time.sleep(1.0)
    try:
        ws2 = WS(f"/ws/{sid}")
        ready2 = ws2.drain("ready", 15)
        record("5a) reconnect ayni sid", ready2 is not None, str(ready2)[:60])
        ws2.send("/status")
        st = ws2.drain("status", 30)
        record("5b) reconnect sonrasi komut", st is not None, str(st)[:90])
        ws2.close()
    except Exception as exc:  # noqa: BLE001
        record("5) reconnect", False, repr(exc))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n[webchat e2e] {passed}/{len(results)} kontrol gecti", flush=True)
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
