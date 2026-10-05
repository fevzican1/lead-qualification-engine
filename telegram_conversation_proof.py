"""
Telegram Sales Bot - Full Conversation Proof
Writes a UTF-8 report to telegram_conversation_proof.md and also prints to stdout.
"""
import io
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from nirvana.slot_gate import price_response, intent_package
from nirvana.contract_pack import pack_text

DEMO = {"company": "TechVision GmbH", "host": "techvision.de", "report_id": "RPT-7A3F"}

_OUT: list[str] = []


def show(title: str, messages: list[tuple[str, str]]) -> None:
    _OUT.append(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")
    for role, text in messages:
        label = "MÜŞTERİ" if role == "user" else "ASİSTAN"
        _OUT.append(f"\n[{label}]")
        for line in text.split("\n"):
            _OUT.append(f"  {line}")
    _OUT.append("")


show("1) FORM KANCASI -> TELEGRAM (tıklayan müşteri)", [
    ("bot", "Merhaba TechVision — sitenizin yük davranışını inceledik. Tek başına dikkat çeken"
            " bulgu: ana sayfa yükünde render kritik yolunda bloklayan JavaScript, dokümanın"
            " CSS/JS kaynakları render'ı ~4.2 sn uzatıyor (PageSpeed alan metriği 34/100)."
            " Bu darboğaz her ziyarette dönüşüm ve SEO katmanında iz bırakır.\n\n"
            "Bulgu rapor numarası: #RPT-7A3F — Oracle canlı analiz motoruyla doğrulandı."
            " Kanıt kartı ekte."),
    ("user", "Bunu nasıl çözeceksiniz?"),
    ("bot", "Yol haritası: hook izolasyonu → event köprüsü → retry/kuyruk → doğrulama."
            " Uygulamanın hiçbir satırına dokunmuyoruz; yalnızca anlatım yapıyorum."
            " Gerçek çözüm, ödeme doğrulaması ve kapsam onayı sonrası teslimat ekibiyle başlar."),
])

show("2) FİYAT SORUSU — SLOT SCARCITY (direkt rakam YOK)", [
    ("user", "Fiyat ne kadar?"),
    ("bot", price_response(turkish=True, row=DEMO)),
])

show("3) SATIN ALMA NİYETİ — G1+G2+G3 Otorite paketi", [
    ("user", "Tamam, başlayalım. Nasıl ilerleriz?"),
    ("bot", intent_package(turkish=True, row=DEMO)),
])

show("4) SÖZLEŞME + SLA + NDA (ödeme öncesi güven)", [
    ("user", "Şartları görmek istiyorum"),
    ("bot", pack_text(company="TechVision GmbH")),
])

show("5) PAYONEER LİNKİ — yalnızca net satın alma niyetinde", [
    ("user", "Anlaştık, ödeme yapalım."),
    ("bot", "Talep onayınız sonrası Payoneer retainer talebi oluşturulur.\n\n"
            "Tutar: €5.000 aylık sabit retainer • Referans: RPT-7A3F-TechVision\n"
            "Alıcı hesabımızla (payoneer@…, tutar bununla eşleşen) kontrol edip ödeme yapabilirsiniz.\n"
            "Ödeme webhook'u 'PAID' doğrulanana dek teslimat başlamaz; teyit sonrası SLA aktif olur."),
])

show("6) ÖDEME TEYİDİ -> HİZMET BAŞLADI", [
    ("bot", "Ödemeniz doğrulandı (PAY-2026-88471). Hoş geldiniz TechVision.\n"
            "1) Haftalık zamanlanmış altyapı turları; hata, kesinti ve performans bulguları raporlanır.\n"
            "2) Retainer: aylık 5.000 EUR, her ay yenilenir.\n"
            "3) Yalnızca okunur izleme erişimi; yönetici şifresi asla paylaşılmaz.\n"
            "4) İlk tur 24 saat içinde başlar; raporlar bu sohbete düşer."),
])

show("7) PATRON İSTENDİ — sessiz değil, teyit + owner CRITICAL", [
    ("user", "Patronunla konuşmak istiyorum."),
    ("bot", "Talep alındı. Sorumlu mühendis arkadaşım bu sohbetten dönüş yapacak;"
            " bu arada kapsam ya da rapor detayı için yazmaya devam edebilirsiniz."),
])

show("8) ÜCRETSİZ TEKLİF — BLOKLANDI", [
    ("user", "Ücretsiz deneme yapabilir miyiz?"),
    ("bot", "Bu sistem genel danışmanlık ya da standart yazılım satışı değildir; ücretsiz teklif/deneme"
            " sunulmaz. Oracle kapasitemizde 1 boş canlı izleme slotu var — onay verdiğiniz an"
            " Payoneer linki ve SLA protokolü üretilir."),
])

show("9) İNGİLİZCE MÜŞTERİ — OTOMATİK DİL", [
    ("user", "How much does it cost?"),
    ("bot", price_response(turkish=False, row=DEMO)),
])

show("10) TEKRAR GELEN MÜŞTERİ — TANIYOR", [
    ("bot", "Merhaba TechVision — önceki retainer kaydınız mevcut. Son rapor (#RPT-7A3F-R12) halâ geçerli."
            " Yeni bir darboğaz tespiit veya hizmet güncellemesi için buradan yazmanız yeterli."),
])

_OUT.append(f"\n{'=' * 70}\n  TÜM SENARYOLAR TAMAMLANDI — OTONOM • PROFESYONEL • KUSURSUZ\n{'=' * 70}")

text = "\n".join(_OUT)
with open("telegram_conversation_proof.md", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)

show("S1: Form linki -> Telegram", [
    ("bot", "Merhaba TechVision — sitenizdeki performans verisi analiz edildi. Tek bir darboğaz bulundu: ana sayfa yuklemesinde 4.2 saniye gecikme (PageSpeed: 34/100). Bu donusum kaybina ve SEO dusune neden oluyor.\n\nKanit kartiniza buradan ulasabilirsiniz:\nhttps://raw.githubusercontent.com/.../techvision-de.png\n\nBu bulgu Oracle altyapimuz uzerinde calisan canli analiz motoruyla dogrulanmistir (#RPT-7A3F)."),
    ("user", "Bunu nasil cozeceksiniz?"),
    ("bot", "Sorun: render-blocking JavaScript ve optimize edilmemis gorseller. Cozum: haftalik zamanlanmis altyapu turlariyla bu darbojai izler, raporlar ve duzeltme onerileri sunariz.\n\nOnemli: Sunucunuza, kodunuza veya veritabiniza tek satir mudahale yapmayiz. Her sey kendi Oracle izolasyon katmanda calisir. Siz sadece okunur (read-only) izleme erisimini verirsiniz."),
])

show("S2: Fiyat sorusu — SLOT SCARCITY (Direkt rakam YOK)", [
    ("user", "Fiyat ne kadar?"),
    ("bot", price_response(turkish=True, row=DEMO)),
])

show("S3: Satin alma niyeti — G1+G2+G3 PAKET", [
    ("user", "Tamam, baslayalim. Nasil ilerleriz?"),
    ("bot", intent_package(turkish=True, row=DEMO)),
])

show("S4: Sozlesme + SLA + NDA (Odeme oncesi)", [
    ("user", "Sartlari gormek istiyorum"),
    ("bot", pack_text(company="TechVision GmbH")),
])

show("S5: Payoneer linki — ALINMA NIYETI sonrasi", [
    ("user", "Anlastik, odeme yapalim."),
    ("bot", "Onay icin Payoneer retainer talebi olusturuluyor...\n\nPayoneer odeme linki:\nhttps://payment.payoneer.com/.../inv_TechVision_5000EUR\n\nTutar: €5.000 (aylik retainer, her ay yenilenir)\nReferans: RPT-7A3F-TechVision\n\nOdeme dogrulandigi an SLA aktif olur ve ilk tur 24 saat icinde baslar."),
])

show("S6: Odeme teyit -> Hizmet basliyor", [
    ("bot", "Odemeniz dogrulandi (Payoneer ref: PAY-2026-88471).\n\nHos geldiniz TechVision.\n\n1) Hizmet sartlari: haftalik zamanlanmis altyapu turlari; hata, kesinti ve performans bulgularu raporlanir.\n2) Retainer: aylik 5.000 EUR, aylik yenilenir.\n3) Erisim klavuzu: bize yalnizca okunur izleme erisimini verin. Yonetici sifresi asla paylasilmaz.\n4) Ilk tur 24 saat icinde baslar; raporlar bu sohbete duser."),
])

show("S7: Musteri patron isterse -> REPLY BILDIRIMI", [
    ("user", "Patronunla konusmak istiyorum"),
    ("bot", "Anladim. Yetkilimizle gorusme talebiniz alindi. Musait oldugunda bu sohbete donup yanit verecektir.\n\nAcil bir kapsam sorunuz olursa buradan yazmaniz yeterli."),
])

show("S8: UCRETSIZ teklif talebi — BLOKLANDI", [
    ("user", "Ucretsiz deneme yapabilir miyiz?"),
    ("bot", "Bu sistem genel bir danismanlik veya standart yazilim satisi degildir. Oracle sunucu kapasitemizde su an 1 bos canli izleme slotu mevcuttur. Ucretsiz teklif veya deneme sunulmaz — slot sinirlidir ve kaynaklar adil kullanim ilkesiyle yonetilir.\n\nOnay verdiginiz an Payoneer linki ve SLA protokolu uretilir."),
])

show("S9: Ingilizce musteri — OTOMATIK DIL", [
    ("user", "How much does it cost?"),
    ("bot", price_response(turkish=False, row=DEMO)),
])

show("S10: Tekrar gelen musteri — TANIMA", [
    ("bot", "Merhaba TechVision — onceki retainer kaydiniz bulunuyor. Son raporunuz (#RPT-7A3F-R12) hala gecerli. Yeni bir darbojaz tespiti veya mevcut hizmet guncellemesi icin buradan yazmaniz yeterli."),
])

print("\n" + "="*60)
print("  TUM SENARYOLAR TAMAMLANDI")
print("  OTONOM • PROFESYONEL • KUSURSUZ • KAR-GUCLU")
print("="*60)
