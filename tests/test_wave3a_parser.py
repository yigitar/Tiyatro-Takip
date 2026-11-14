"""Wave 3a: play summary / cast / credits extracted from the Biletinial detail page."""
from pathlib import Path

from tiyatro.parser import parse_play, tr_title

FIX = Path(__file__).parent / "fixtures"
BASE = "https://biletinial.com/tr-tr/tiyatro/"


def play(name, slug="x"):
    return parse_play((FIX / name).read_text(encoding="utf-8"), BASE + slug)


def test_on_ikinci_gece_cast_from_caps_paragraph():
    p = play("play_on_ikinci_gece.html")
    assert p.cast == [
        "İbrahim Aydın", "İlker Bağlam", "Sarper Saydam", "Senem Akman Muratoğlu",
        "Sevcan Kuş Efe", "Su Özdemir", "Tuğra Can Bıçak", "Ufuk Tuncer",
        "Volkan Dinç", "Yusuf Ferhat Kanat", "Çağla Akars"]


def test_on_ikinci_gece_credits_cap_and_labels():
    p = play("play_on_ikinci_gece.html")
    assert len(p.credits) == 12
    assert p.credits["Yazan"] == "William Shakespeare"
    assert p.credits["Çeviren"] == "Zeynep Avcı"
    assert p.credits["Yönetmen"] == "Burç Güray Dinçol"
    assert p.credits["Dekor Tasarım"] == "Nur Sinem Mete"
    assert p.credits["Yardımcı Yönetmen"] == "Nursel Çeliktürk"
    assert list(p.credits)[:3] == ["Yazan", "Çeviren", "Yönetmen"]


def test_on_ikinci_gece_summary_excludes_cast_and_credits():
    s = play("play_on_ikinci_gece.html").summary
    assert s.startswith("Rüzgâr uğuldar, deniz yırtılır\nGece ikiye bölünür durmaksızın")
    assert "Ve William Shakespeare'den On İkinci Gece başlar." in s
    assert "\n\nKorkunç bir deniz kazası" in s
    assert "OYUNCULAR" not in s and "İBRAHİM" not in s and "Yönetmen" not in s
    assert not s.startswith("On İkinci Gece Tiyatro Oyunu")  # title-only heading dropped


def test_hic_kimsenin_has_summary_but_no_cast():
    p = play("play_hic_kimsenin.html")
    assert p.summary.startswith("Bir tren kompartımanında yolları kesişen iki hayat...")
    assert p.summary.endswith("İyi seyirler…")
    assert "Hiç Kimsenin Öyküsü" not in p.summary
    assert p.cast == [] and p.credits == {}


def test_kusursuz_structured_cast_and_crew():
    p = play("play_kusursuz.html")
    assert len(p.cast) == 11
    assert p.cast[:3] == ["Ezgi Özbalı", "Güliz Gençoğlu", "Utku Oğuz"]  # page order, whitespace folded
    assert p.cast[-1] == "Tunç Efe"
    assert p.credits["Yönetmen"] == "Gaye Çetinkaya"
    assert p.credits["Dekor Tasarım"] == "Efter Tunç"
    assert len(p.credits) <= 12
    assert p.summary.startswith("Kiraz, “Kusursuz Dünya” adlı bilgisayar oyununa")


def test_other_fixtures_do_not_crash_and_have_types():
    for name in ("play_on_sale.html", "play_on_sale_real.html"):
        p = play(name)
        assert p.summary is None or isinstance(p.summary, str)
        assert isinstance(p.cast, list) and isinstance(p.credits, dict)


def _page(about: str) -> str:
    base = (FIX / "play_hic_kimsenin.html").read_text(encoding="utf-8")
    a = base.index('id="event-card-v2-about">') + len('id="event-card-v2-about">')
    b = base.index("</div>", a)
    return base[:a] + about + base[b:]


def test_summary_robust_whitespace_entities_and_cap():
    p = parse_play(_page("<p>  Merhaba   &amp;\n  d&#252;nya <b>x</b> </p>\n\n<p>ikinci</p>"), BASE + "x")
    assert p.summary == "Merhaba & dünya x\n\nikinci"
    long = " ".join(["kelime"] * 600)
    s = parse_play(_page(f"<p>{long}</p>"), BASE + "x").summary
    assert len(s) <= 1500 and s.endswith("…") and not s[:-1].endswith(" ")
    assert s[:-1].split(" ")[-1] == "kelime"


def test_missing_about_block_gives_none_and_empty():
    base = (FIX / "play_hic_kimsenin.html").read_text(encoding="utf-8")
    html = base.replace('event-card-v2__about', 'zzz')
    p = parse_play(html, BASE + "x")
    assert p.summary is None and p.cast == [] and p.credits == {}
    p = parse_play(_page(""), BASE + "x")
    assert p.summary is None


def test_cast_with_role_and_credit_cap():
    crew = "<br />\n".join(f"<strong>Rol{i}</strong>: ADA {i}" for i in range(20))
    about = ("<p><strong>OYUNCULAR</strong><br />\nAYŞE YILMAZ (Hamlet),\nMEHMET ÖZ</p>"
             f"<p>{crew}</p>")
    p = parse_play(_page(about), BASE + "x")
    assert p.cast == ["Ayşe Yılmaz (Hamlet)", "Mehmet Öz"]
    assert len(p.credits) == 12


def test_mixed_case_names_are_kept_as_is():
    assert tr_title("Ayşe Yılmaz") == "Ayşe Yılmaz"
    assert tr_title("İBRAHİM AYDIN") == "İbrahim Aydın"
    assert tr_title("IŞIK") == "Işık"
    assert tr_title("WILLIAM SHAKESPEARE") == "William Shakespeare"


# ---- fix round 1 ----------------------------------------------------------------------------

def test_real_fixture_details_are_unchanged():
    import json
    expected = json.loads((FIX / "expected_details_wave3a.json").read_text(encoding="utf-8"))
    for name, want in expected.items():
        p = play(name + ".html")
        assert {"cast": p.cast, "credits": p.credits, "summary": p.summary} == want, name


def test_tr_title_word_starts_after_separators():
    assert tr_title("CEM KAHRAMAN, EFKAN DEVELİ") == "Cem Kahraman, Efkan Develi"
    assert tr_title("JEAN-LUC GODARD") == "Jean-Luc Godard"
    assert tr_title("AYŞE/MEHMET ÖZ") == "Ayşe/Mehmet Öz"
    assert tr_title("TOM & JERRY") == "Tom & Jerry"
    assert tr_title("ALİ (ZEYBEK)") == "Ali (Zeybek)"
    assert tr_title("ALİ (ZEYBEK), VELİ") == "Ali (Zeybek), Veli"
    assert tr_title("O'NEIL") == "O'Neil"
    assert tr_title("D'ARTAGNAN") == "D'Artagnan"
    assert tr_title("ANKARA'DA OYNAR") == "Ankara'da Oynar"


def test_tr_title_capital_i_by_vowel_harmony():
    assert tr_title("DILEK KAPLAN") == "Dilek Kaplan"
    assert tr_title("ZEYNEP AVCI") == "Zeynep Avcı"
    assert tr_title("DICKENS") == "Dickens"
    assert tr_title("MOLIERE") == "Moliere"
    assert tr_title("NIKOLAI GOGOL") == "Nikolai Gogol"
    assert tr_title("AZİZ TEMİZARABACI") == "Aziz Temizarabacı"  # explicit İ: Turkish


def test_prose_with_trailing_credit_line_does_not_leak_into_summary():
    p = parse_play(_page("<p>Metin<br/><strong>Yazan</strong>: X Y</p>"), BASE + "x")
    assert p.summary == "Metin" and p.credits == {"Yazan": "X Y"}


def test_nested_div_in_about_block_does_not_cut_the_summary():
    p = parse_play(_page("<div><p>nested</p></div><p>after</p>"), BASE + "x")
    assert "after" in p.summary and "nested" in p.summary


def test_cast_label_and_names_on_one_line():
    p = parse_play(_page("<p><strong>Oyuncular:</strong> ALİ, VELİ</p>"), BASE + "x")
    assert p.cast == ["Ali", "Veli"] and p.credits == {}
    p = parse_play(_page("<p><strong>OYUNCULAR</strong>: ALİ; VELİ</p>"), BASE + "x")
    assert p.cast == ["Ali", "Veli"] and p.credits == {}
