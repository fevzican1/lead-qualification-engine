"""
Telegram Sales Bot - Full Conversation Proof
"""
from nirvana.slot_gate import diagnostic_gate, zero_resource, reservation, price_response, intent_package
from nirvana.contract_pack import pack_text
from nirvana.payment import price_retainer, retainer_label

DEMO = {"company": "TechVision GmbH", "host": "techvision.de", "report_id": "RPT-7A3F"}

def show(title, messages):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")
    for role, text in messages:
        label = "ASISTAN" if role == "bot" else "MUSTERI"
        print(f"\n[{label}]")
        for line in text.split("\n"):
            print(f"  {line}")
    print()

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
    ("bot", "Onay icin Payoneer retainer talebi olusturuluyor...\n\nPayoneer odeme linki:\nhttps://payment.payoneer.com/.../inv_TechVision_2500EUR\n\nTutar: €2.500 (aylik retainer, her ay yenilenir)\nReferans: RPT-7A3F-TechVision\n\nOdeme dogrulandigi an SLA aktif olur ve ilk tur 24 saat icinde baslar."),
])

show("S6: Odeme teyit -> Hizmet basliyor", [
    ("bot", "Odemeniz dogrulandi (Payoneer ref: PAY-2026-88471).\n\nHos geldiniz TechVision.\n\n1) Hizmet sartlari: haftalik zamanlanmis altyapu turlari; hata, kesinti ve performans bulgularu raporlanir.\n2) Retainer: aylik 2.500 EUR, aylik yenilenir.\n3) Erisim klavuzu: bize yalnizca okunur izleme erisimini verin. Yonetici sifresi asla paylasilmaz.\n4) Ilk tur 24 saat icinde baslar; raporlar bu sohbete duser."),
])

show("S7: Musteri patron isterse → REPLY BILDIRIMI", [
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
