"""Per-language roster tables: club and player names in Arabic and Devanagari script.

Names fill Caller and claim templates from these tables by id; no model ever writes a name at
runtime. Every generated name is built from a given name and two surname morphemes
(``earshot.world.names``), so each piece has one fixed rendering per script below and the full
name is assembled deterministically. Spanish and English use the Latin names unchanged.

The renderings were written by an AI model (Claude) and spot-checked by the same kind of model:
20 names per script are listed in ``reports/roster-spotcheck.md``. No native reader has reviewed
them yet, which is a stated limit (design R3-32).
"""

from __future__ import annotations

from functools import cache

from earshot.contract.line import Language
from earshot.contract.match import ClubInfo, PlayerInfo, Roster
from earshot.world.clubs import Club, Player, world
from earshot.world.names import PersonName

AR, HI = 0, 1


def _table(spec: str) -> dict[str, tuple[str, str]]:
    out = {}
    for line in spec.strip().splitlines():
        latin, ar, hi = (x.strip() for x in line.split("|"))
        out[latin] = (ar, hi)
    return out


GIVEN = _table("""
Callum | كالوم | कैलम
Ewan | يوان | यूअन
Jody | جودي | जोडी
Rhys | ريس | रीस
Owen | أوين | ओवेन
Theo | ثيو | थियो
Mason | ميسون | मेसन
Reece | ريس | रीस
Kieran | كيران | कीरन
Lewis | لويس | लुईस
Toby | توبي | टोबी
Ellis | إليس | एलिस
Ronan | رونان | रोनन
Jamie | جيمي | जेमी
Alfie | ألفي | अल्फ़ी
Corey | كوري | कोरी
Mateo | ماتيو | मातेओ
Iker | إيكر | इकर
Thiago | تياغو | तियागो
Bruno | برونو | ब्रूनो
Dario | داريو | दारियो
Emilio | إميليو | एमिलियो
Gael | غايل | गाएल
Hugo | هوغو | ह्यूगो
Joaquin | خواكين | होआकिन
Lisandro | ليساندرو | लिसांद्रो
Nahuel | ناهويل | नाहुएल
Rodrigo | رودريغو | रोड्रिगो
Santino | سانتينو | सांतिनो
Tomas | توماس | तोमास
Valentin | فالنتين | वैलेंटिन
Ulises | أوليسيس | उलीसेस
Joao | جواو | जोआओ
Rafael | رافائيل | राफ़ाएल
Caio | كايو | काइओ
Davi | دافي | दावी
Enzo | إنزو | एंज़ो
Igor | إيغور | इगोर
Luan | لوان | लुआन
Murilo | موريلو | मुरीलो
Otavio | أوتافيو | ओटावियो
Renan | رينان | रेनान
Tiago | تياغو | तियागो
Vitor | فيتور | वितोर
Bastien | باستيان | बास्तियां
Corentin | كورنتان | कोरेंतां
Elouan | إيلوان | एलुआं
Florian | فلوريان | फ़्लोरियां
Gaetan | غايتان | गाएतां
Jordan | جوردان | जॉर्डन
Killian | كيليان | किलियन
Loic | لويك | लोइक
Maxence | ماكسانس | मैक्सांस
Nolan | نولان | नोलन
Quentin | كانتان | कांतां
Romain | رومان | रोमां
Tanguy | تانغي | तांगी
Yanis | يانيس | यानिस
Adewale | أديوالي | अदेवाले
Chidi | تشيدي | चिडी
Emeka | إيميكا | एमेका
Femi | فيمي | फ़ेमी
Ikenna | إيكينا | इकेन्ना
Kwabena | كوابينا | क्वाबेना
Kofi | كوفي | कोफ़ी
Obinna | أوبينا | ओबिन्ना
Seun | سيون | सेउन
Tunde | توندي | टुंडे
Yaw | ياو | याओ
Kwame | كوامي | क्वामे
Ebuka | إيبوكا | एबुका
Dayo | دايو | दायो
Karim | كريم | करीम
Yassin | ياسين | यासीन
Hamza | حمزة | हमज़ा
Ilyas | إلياس | इलियास
Rayan | ريان | रयान
Samir | سمير | समीर
Tarek | طارق | तारिक़
Walid | وليد | वलीद
Zakaria | زكريا | ज़करिया
Adel | عادل | आदिल
Bilal | بلال | बिलाल
Nabil | نبيل | नबील
Hicham | هشام | हिशाम
Anas | أنس | अनस
Arjun | أرجون | अर्जुन
Dev | ديف | देव
Ishaan | إيشان | ईशान
Kabir | كبير | कबीर
Nikhil | نيخيل | निखिल
Rohan | روهان | रोहन
Sahil | ساحل | साहिल
Tanvir | تنوير | तनवीर
Vikram | فيكرام | विक्रम
Aarav | آراف | आरव
Imran | عمران | इमरान
Harjit | هارجيت | हरजीत
Pranav | براناف | प्रणव
Ravi | رافي | रवि
Bogdan | بوغدان | बोगदान
Damir | دامير | दामिर
Filip | فيليب | फ़िलिप
Goran | غوران | गोरान
Ivo | إيفو | इवो
Luka | لوكا | लूका
Marko | ماركو | मार्को
Nikola | نيكولا | निकोला
Petar | بيتار | पेतार
Stipe | ستيبي | स्टीपे
Vuk | فوك | वुक
Zoran | زوران | ज़ोरान
Darko | داركو | दार्को
Mirko | ميركو | मिर्को
Anders | أندرس | एंडर्स
Birk | بيرك | बिर्क
Eskil | إسكيل | एस्किल
Halvar | هالفار | हालवार
Jonas | يوناس | योनास
Leif | ليف | लेफ़
Magnus | ماغنوس | मैग्नस
Oskar | أوسكار | ऑस्कर
Sindre | سيندري | सिंद्रे
Tobias | توبياس | टोबियास
Viggo | فيغو | विगो
Elias | إلياس | एलियास
""")

# Surname prefixes are word-initial; suffixes follow a prefix. Keys are "tradition:morpheme".
PREFIX = _table("""
anglo:Ash | آش | ऐश
anglo:Brad | براد | ब्रैड
anglo:Carr | كار | कार
anglo:Dun | دن | डन
anglo:Fen | فين | फ़ेन
anglo:Gar | غار | गार
anglo:Hal | هال | हैल
anglo:Kel | كيل | केल
anglo:Lang | لانغ | लैंग
anglo:Mar | مار | मार
anglo:Pen | بين | पेन
anglo:Ross | روس | रॉस
anglo:Shel | شيل | शेल
anglo:Thorn | ثورن | थॉर्न
anglo:Wex | ويكس | वेक्स
anglo:Yar | يار | यार
hispanic:Var | فار | वार
hispanic:Mon | مون | मोन
hispanic:Cal | كال | काल
hispanic:Bel | بيل | बेल
hispanic:Ord | أورد | ओर्द
hispanic:Sal | سال | साल
hispanic:Ter | تير | तेर
hispanic:Quin | كين | किन
hispanic:Alv | ألف | अल्व
hispanic:Bar | بار | बार
hispanic:Cas | كاس | कास
hispanic:Fer | فير | फ़ेर
hispanic:Gal | غال | गाल
hispanic:Lor | لور | लोर
hispanic:Mir | مير | मीर
hispanic:Rib | ريب | रिब
lusophone:Ar | أر | अर
lusophone:Bra | برا | ब्रा
lusophone:Cor | كور | कोर
lusophone:Fon | فون | फ़ोन
lusophone:Gou | غو | गो
lusophone:Lob | لوب | लोब
lusophone:Mac | ماك | माक
lusophone:Nog | نوغ | नोग
lusophone:Per | بير | पेर
lusophone:Qua | كوا | क्वा
lusophone:Ser | سير | सेर
lusophone:Tav | تاف | ताव
francophone:Bou | بو | बू
francophone:Char | شار | शार
francophone:Del | ديل | देल
francophone:Fav | فاف | फ़ाव
francophone:Gir | جير | जीर
francophone:Lam | لام | लाम
francophone:Mor | مور | मोर
francophone:Pel | بيل | पेल
francophone:Roch | روش | रोश
francophone:Tess | تيس | तेस
francophone:Vall | فال | वाल
francophone:Lec | لوك | लेक
west_african:Ade | أدي | अदे
west_african:Oko | أوكو | ओको
west_african:Nwo | نو | न्वो
west_african:Ola | أولا | ओला
west_african:Afo | أفو | अफ़ो
west_african:Eze | إيزي | एज़े
west_african:Iwu | إيو | इवु
west_african:Obi | أوبي | ओबी
west_african:Asa | أسا | असा
west_african:Ogu | أوغو | ओगु
west_african:Ama | أما | अमा
west_african:Kel | كيل | केल
arabic:Bou | بو | बू
arabic:Ham | حم | हम
arabic:Kha | خا | ख़ा
arabic:Mans | منص | मंस
arabic:Nas | نص | नस
arabic:Sab | صب | सब
arabic:Tah | طه | तह
arabic:Zer | زر | ज़ेर
arabic:Fadl | فضل | फ़ज़्ल
arabic:Jab | جب | जब
arabic:Mak | مك | मक
arabic:Raf | رف | रफ़
south_asian:Bha | بها | भा
south_asian:Cha | تشا | चा
south_asian:Desh | ديش | देश
south_asian:Kap | كاب | कप
south_asian:Mal | مال | मल
south_asian:Nar | نار | नर
south_asian:Pat | بات | पट
south_asian:Rav | راف | रव
south_asian:Sha | شا | शा
south_asian:Tal | تال | तल
south_asian:Vas | فاس | वस
south_asian:Jai | جاي | जय
slavic:Kov | كوف | कोव
slavic:Mil | ميل | मिल
slavic:Rad | راد | राद
slavic:Pet | بيت | पेत
slavic:Jan | يان | यान
slavic:Stan | ستان | स्तान
slavic:Vel | فيل | वेल
slavic:Dra | درا | द्रा
slavic:Bor | بور | बोर
slavic:Zel | زيل | ज़ेल
slavic:Kra | كرا | क्रा
slavic:Lub | لوب | लुब
nordic:Berg | بيرغ | बर्ग
nordic:Dahl | دال | डाल
nordic:Hol | هول | होल
nordic:Lind | ليند | लिंड
nordic:Nor | نور | नूर
nordic:Sand | ساند | सैंड
nordic:Vik | فيك | विक
nordic:Ask | أسك | आस्क
nordic:Ek | إيك | एक
nordic:Fal | فال | फ़ाल
nordic:Hed | هيد | हेड
nordic:Lund | لوند | लुंड
""")

SUFFIX = _table("""
anglo:worth | ورث | वर्थ
anglo:ley | لي | ली
anglo:mere | مير | मियर
anglo:by | بي | बी
anglo:well | ويل | वेल
anglo:ard | ارد | ार्ड
anglo:ing | ينغ | िंग
anglo:ock | وك | ॉक
anglo:man | مان | मैन
anglo:er | ير | र
anglo:ridge | ريدج | रिज
anglo:cott | كوت | कॉट
hispanic:era | يرا | ेरा
hispanic:illa | يا | ीया
hispanic:ado | ادو | ादो
hispanic:edo | يدو | ेदो
hispanic:ana | انا | ाना
hispanic:ales | اليس | ालेस
hispanic:ero | يرو | ेरो
hispanic:oza | وسا | ोसा
hispanic:ino | ينو | ीनो
hispanic:ente | ينتي | ेंते
hispanic:aro | ارو | ारो
hispanic:uela | ويلا | ुएला
lusophone:anha | انيا | ान्या
lusophone:eira | يرا | ेइरा
lusophone:elo | يلو | ेलो
lusophone:inho | ينيو | ीन्यो
lusophone:oso | وزو | ोज़ो
lusophone:ais | ايس | ाइस
lusophone:oto | وتو | ोतो
lusophone:ado | ادو | ादो
francophone:ard | ار | ार
francophone:eau | و | ो
francophone:ier | يه | िए
francophone:on | ون | ों
francophone:ault | و | ो
francophone:elle | يل | ेल
francophone:et | يه | े
francophone:ain | ان | ां
francophone:ot | و | ो
francophone:eux | و | ो
west_african:yemi | يمي | येमी
west_african:nkwo | نكوو | न्क्वो
west_african:bisi | بيسي | बीसी
west_african:for | فور | फ़ोर
west_african:chukwu | تشوكوو | चुक्वु
west_african:wale | والي | वाले
west_african:lade | لادي | लादे
west_african:nna | نا | न्ना
west_african:toye | تويي | तोये
west_african:mah | ماه | मह
west_african:gbe | غبي | ग्बे
west_african:du | دو | दु
arabic:ari | اري | ारी
arabic:idi | يدي | ीदी
arabic:oun | ون | ून
arabic:ani | اني | ानी
arabic:ouri | وري | ूरी
arabic:adi | ادي | ादी
arabic:ini | يني | ीनी
arabic:ane | ان | ान
arabic:ir | ير | ीर
arabic:ali | الي | ाली
arabic:oud | ود | ूद
arabic:ira | يرة | ीरा
south_asian:wani | واني | वानी
south_asian:ndra | ندرا | न्द्र
south_asian:kar | كار | कर
south_asian:ore | ور | ोर
south_asian:wal | وال | वाल
south_asian:ana | انا | ाना
south_asian:deo | ديو | देव
south_asian:iya | يا | िया
south_asian:ani | اني | ानी
south_asian:nath | ناث | नाथ
south_asian:esh | يش | ेश
south_asian:var | فار | वर
slavic:ovic | وفيتش | ोविच
slavic:enko | ينكو | ेंको
slavic:ski | سكي | स्की
slavic:ic | يتش | िच
slavic:ar | ار | ार
slavic:ek | يك | ेक
slavic:ov | وف | ोव
slavic:ec | يتس | ेत्स
slavic:anik | انيك | ानिक
slavic:usic | وسيتش | ुसिच
nordic:gren | غرين | ग्रेन
nordic:qvist | كفيست | क्विस्ट
nordic:mark | مارك | मार्क
nordic:strand | ستراند | स्ट्रैंड
nordic:holm | هولم | होल्म
nordic:sen | سن | सेन
nordic:vold | فولد | वोल्ड
nordic:dal | دال | डाल
nordic:rud | رود | रुड
""")

CLUBS = _table("""
kes | كيستريلز | केस्ट्रल्स
cop | كوبرلاين | कॉपरलाइन
tid | تايدواردنز | टाइडवार्डन्स
sal | سولتوينغز | सॉल्टविंग्स
gla | غلاسرايتس | ग्लासराइट्स
riv | ريفركايتس | रिवरकाइट्स
sto | ستونكروز | स्टोनक्रोज़
map | ميبلغارد | मेपलगार्ड
wea | ويفربيردز | वीवरबर्ड्स
fli | فلينتلوكس | फ़्लिंटलॉक्स
cin | سيندرجاكس | सिंडरजैक्स
sil | سيلفرستاغز | सिल्वरस्टैग्स
spa | سباركويل | स्पार्कव्हील
dun | ديونريسرز | ड्यूनरेसर्स
oak | أوكميسونز | ओकमेसन्स
rav | رايفنمارك | रेवनमार्क
bel | بيلفورج | बेलफ़ोर्ज
wav | ويفكاترز | वेवकटर्स
fer | فيرنباكس | फ़र्नबैक्स
lan | لانترنز | लैंटर्न्स
""")

_DEV_SIGNS = {
    "ा": "आ",
    "ि": "इ",
    "ी": "ई",
    "ु": "उ",
    "ू": "ऊ",
    "े": "ए",
    "ै": "ऐ",
    "ो": "ओ",
    "ौ": "औ",
    "ॉ": "ऑ",
    "ं": "अं",
}
_DEV_VOWEL_END = set("ािीुूेैोौॉअआइईउऊएऐओऔऑ")
_AR_VOWEL_START = set("اوي")


def _join(script: int, prefix: str, suffix: str, merged: bool) -> str:
    """Join two morphemes; ``merged`` mirrors the Latin rule that drops a doubled seam letter."""
    if script == HI:
        if suffix and suffix[0] in _DEV_SIGNS:
            if merged:
                suffix = suffix[1:]
            elif prefix and prefix[-1] in _DEV_VOWEL_END:
                suffix = _DEV_SIGNS[suffix[0]] + suffix[1:]
        return prefix + suffix
    if merged and suffix and suffix[0] in _AR_VOWEL_START and len(suffix) > 1:
        suffix = suffix[1:]
    return prefix + suffix


def _merged(p: str, s: str) -> bool:
    return p[-1].lower() == s[0].lower() and s[0].lower() not in "lsnr"


def surname_in(name: PersonName, script: int) -> str:
    p = PREFIX[f"{name.tradition}:{name.prefix}"][script]
    s = SUFFIX[f"{name.tradition}:{name.suffix}"][script]
    return _join(script, p, s, _merged(name.prefix, name.suffix))


def full_name_in(name: PersonName, script: int) -> str:
    return f"{GIVEN[name.given][script]} {surname_in(name, script)}"


def _script(language: Language) -> int | None:
    return {Language.AR: AR, Language.HI: HI}.get(language)


def player_info(p: Player, language: Language) -> PlayerInfo:
    script = _script(language)
    if script is None:
        return p.info()
    return p.info().model_copy(
        update={"name": full_name_in(p.name, script), "short_name": surname_in(p.name, script)}
    )


def club_info(c: Club, language: Language) -> ClubInfo:
    script = _script(language)
    if script is None:
        return c.info()
    local = CLUBS[c.team_id][script]
    return c.info().model_copy(update={"name": local, "short_name": local})


@cache
def roster(language: Language) -> Roster:
    """The league roster with names in the language's script (Latin for en and es)."""
    w = world()
    return Roster(
        clubs=[club_info(c, language) for c in w.clubs],
        players=[player_info(p, language) for c in w.clubs for p in c.players],
    )


def spotcheck_markdown(n: int = 20, seed: int = 20261016) -> str:
    """The roster spot check (R3-32): ``n`` seeded random players per script, for a reader to review."""
    import numpy as np

    w = world()
    players = [p for c in w.clubs for p in c.players]
    idx = np.random.default_rng(seed).choice(len(players), n, replace=False)
    lines = [
        "# Roster spot check (Arabic and Devanagari)",
        "",
        f"{n} players drawn with seed {seed} from the 500 in the league, plus all 20 clubs. The renderings "
        "come from the morpheme tables in `src/earshot/world/localize.py`, written by an AI model (Claude). "
        "**Review status: model-only.** The same kind of model read them; no native reader has yet. That is "
        "a stated limit (design R3-32). Fixes found by the model pass are recorded in the git history.",
        "",
        "| Tradition | Latin | Arabic | Devanagari |",
        "|---|---|---|---|",
    ]
    for i in idx:
        p = players[int(i)]
        ar, hi = full_name_in(p.name, AR), full_name_in(p.name, HI)
        lines.append(f"| {p.name.tradition} | {p.name.full} | {ar} | {hi} |")
    lines += ["", "| Club | Arabic | Devanagari |", "|---|---|---|"]
    lines += [f"| {c.name} | {CLUBS[c.team_id][AR]} | {CLUBS[c.team_id][HI]} |" for c in w.clubs]
    return "\n".join(lines) + "\n"
