from __future__ import annotations

from typing import Any

from nirvana.payment import retainer_label


def build_pack(*, company: str = "", domain: str = "") -> dict[str, str]:
    """Pre-signed NDA + SLA + proforma invoice + Payoneer escrow guarantee pack.

    Müşteriye sunulan kurumsal risk sıfırlama paketi:
    - Form gönderildiği anda otomatik olarak hazırlanan önceden imzalanmış NDA
    - Hizmet seviyesi taahhüdü (SLA) timing/penalty detayları ile
    - €5.000 proforma fatura
    - Payoneer B2B escrow güvencesi metni
    """
    who = company.strip() or domain.strip() or "Müşteri"
    price = retainer_label()
    return {
        "nda": (
            f"Gizlilik Sözleşmesi (Pre-signed) — {who}\n"
            "Bu belge, form gönderiminde otomatik olarak hazırlanmış ve onaylanmış bir Gizlilik Sözleşmesi temsil eder.\n"
            "Müşteri tarafında herhangi bir further action gerektirmediği sürece, SLA ve hizmet başlama koşulları bu belge ile\n"
            "özde consider edilmiş sayılır.\n"
            "\n"
            "Pre-signed Gizlilik Sözleşmesi (NDA) — {who} için otomatik hazırlanmıştır.\n"
            "İlgili tarafların gizlilik yükümlülükleri bu sözleşme ile erfüllt edilmiş sayılır.\n"
        ),
        "sla": (
            f"Hizmet Seviyesi Taahhüdü (SLA) — {who}\n"
            f"1) Kapsam: Whitelabel ortaklık lisansı ve otomatize teslimat altyapısı.\n"
            f"2) Kurulum süresi: Ödeme teyidi alınan {price} anda 24 saat içinde altyapı provisioning başlar.\n"
            f"3) Performans kriterleri: Weekly infrastructure sweep; herhangi bir SLA ihlali 48 saat içinde raporlanır.\n"
            f"4) Cezai şartlar: SLA şartları çerçevesinde Whitelabel altyapısı teslim edildiğinde Payoneer escrow onaylanır.\n"
            f"\n"
            f"SLA metni: Sistemin ne kadar sürede kurulacağını, performans kriterlerini ve hedeflenen çıktıların gerçekleşmemesi\n"
            f"durumu geçerli olacak cezai şartları net bir şekilde belirler.\n"
        ),
        "proforma_invoice": (
            f"Proforma Fatura — {who}\n"
            f"Tutar: {price} (tek seferlik Whitelabel ortaklık lisans bedeli)\n"
            f"Ödeme kanalı: Payoneer B2B Ödeme Güvenliği (escrow havuzu)\n"
            f"Garanti: Müşteriye sunulan 'Ödemeniz Payoneer güvenceli havuz hesabında tutulmakta, SLA şartları\n"
            f"çerçevesinde Whitelabel altyapısı teslim edildiğinde onaylanmaktadır' garantisi.\n"
            f"\n"
            f"Fiyatlandırma süreçlerinde hiçbir sözlü bilgilendirme yapılmaz; doğrudan {price}\n"
            f"resmi proforma fatura düzenlenir.\n"
        ),
        "payoneer_escrow": (
            f"Payoneer B2B Ödeme Güvencesi — {who}\n"
            f"Ödeme kanalı: uluslararası geçerliliğe sahip B2B escrow altyapısı (Payoneer)\n"
            f"Garanti: Müşteriye sunulan 'Ödemeniz Payoneer güvenceli havuz hesabında tutulmakta, SLA şartları\n"
            f"çerçevesinde Whitelabel altyapısı teslim edildiğinde onaylanmaktadır' garantisi, ücretsiz deneme taleplerini\n"
            f"anlamsız kılarken %100 finansal emniyet sağlar.\n"
            f"\n"
            f"Fiyatlandırma: Sözleşme herhangi bir sözlü bilgilendirme içermez; doğrudan {price} tutarında fatura\n"
            f"düzenlenir ve Payoneer escrow havuzuna bağlanır.\n"
        ),
    }


def pack_text(*, company: str = "", domain: str = "", email: str = "", name: str = "") -> str:
    pack = build_pack(company=company, domain=domain)
    text = "\n\n".join([pack["nda"], pack["sla"], pack["proforma_invoice"], pack["payoneer_escrow"]])
    # Rapor 2.2 / 4.3: Documenso v2 hazirsa gercek e-imza baglantisi eklenir.
    # Yapilandirilmamisken (veya hata halinde) metin aynen kalir — fail-open.
    link = ""
    try:
        from nirvana import esign_documenso

        if email:
            link = esign_documenso.signing_link(company=company, email=email, name=name)
    except Exception:  # noqa: BLE001 — sozlesme akisini asla dusurmez
        link = ""
    if link:
        text += f"\n\nE-imza baglantisi (Documenso): {link}"
    return text