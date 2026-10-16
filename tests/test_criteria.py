import json
import re
from pathlib import Path

import pytest
from ajh import criteria as C
from ajh.model import Job

PLACES = C.Places.bundled()
MINIMAL = {"schema_version": 1, "targets": {"roles": ["UX Researcher"]}}
TABLE = Path(C.__file__).with_name("data") / "places.json"
ISO_3166_1 = set(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ "
    "BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM "
    "DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS "
    "GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN "
    "KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ "
    "MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM "
    "PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV "
    "SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI "
    "VN VU WF WS YE YT ZA ZM ZW".split()
)


def job(**changes):
    facts = dict(
        key="acme:1",
        source="acme",
        source_id="1",
        company="Acme",
        title="UX Researcher",
        location="Remote, US",
        url="https://jobs.example.com/1",
        description="Run studies.",
    )
    facts.update(changes)
    return Job(**facts)


def test_a_minimal_file_gets_every_default():
    out = C.normalize(MINIMAL, PLACES)
    assert out["targets"] == {"roles": ["UX Researcher"], "adjacent": [], "avoid": []}
    assert out["caps"] == {"max_triage": 1500, "max_assess": 100, "max_attempts": 3, "max_usd_per_month": None}
    assert out["backfill"] == {"days": None} and out["employment"] == [] and out["locations"]["regions"] == []
    assert C.normalize(dict(MINIMAL, backfill={"days": 45}), PLACES)["backfill"]["days"] == 45


def test_every_problem_is_reported_at_once():
    bad = {
        "schema_version": 2,
        "targets": {"roles": [], "adjcent": ["x"]},
        "employment": ["full-time"],
        "locations": {"regions": ["US", "Atlantis"], "remote": "sometimes"},
        "thresholds": {"strong": 50, "good": 60},
        "caps": {"max_assess": 0},
        "colour": "blue",
    }
    with pytest.raises(C.CriteriaError) as failure:
        C.normalize(bad, PLACES)
    message = str(failure.value)
    for expected in (
        "schema_version",
        "targets.adjcent",
        "at least one role",
        "employment must list",
        "Atlantis",
        "locations.remote",
        "strong >= good",
        "caps must be positive",
        "'colour'",
    ):
        assert expected in message


@pytest.mark.parametrize(
    "label,expected",
    [
        ("FullTime", {"full_time"}),
        ("Full-time", {"full_time"}),
        ("fulltime_permanent", {"full_time"}),
        ("fulltime_fixed_term", {"full_time", "temporary"}),
        ("Part-time", {"part_time"}),
        ("Intern", {"internship"}),
        ("Internship", {"internship"}),
        ("Contractor", {"contract"}),
        ("Working Student", {"internship"}),
        ("unknown", set()),
        ("", set()),
        (None, set()),
        ("International", set()),
    ],
)
def test_employment_labels_map_to_every_kind_they_could_mean(label, expected):
    assert C.employment_kinds(label) == expected


@pytest.mark.parametrize(
    "location,countries,complete",
    [
        ("Remote, US", {"US"}, True),
        ("United States", {"US"}, True),
        ("US, WA, Bellevue", {"US"}, True),
        ("New York, NY", {"US"}, True),
        ("San Francisco, CA", {"US"}, True),
        ("San Francisco, CA • New York, NY • United States", {"US"}, True),
        ("San Francisco Bay Area", {"US"}, True),
        ("Chicago, IL", {"US"}, True),
        ("Tel Aviv, IL", {"IL"}, True),
        ("Bengaluru, IN", {"IN"}, True),
        ("Toronto, CA", {"CA"}, True),
        ("Vancouver, WA", {"US"}, True),
        ("London, ON", {"CA"}, True),
        ("London", {"GB"}, True),
        ("Hybrid - London, UK", {"GB"}, True),
        ("Birmingham, United Kingdom / Belfast / Homeworker - UK", {"GB"}, True),  # a Workday record's places
        ("Cardiff", {"GB"}, True),
        ("Singapore, 01, sg", {"SG"}, True),
        ("Guangzhou, GD, CN", {"CN"}, True),
        ("Remote (Germany)", {"DE"}, True),
        ("US-Remote", {"US"}, True),
        ("New York / Berlin", {"US", "DE"}, True),
        ("Amsterdam, Noord-Holland, Netherlands", {"NL"}, True),
        ("Germany; Remote", {"DE"}, False),
        ("Remote", set(), False),
        ("Worldwide", set(), False),
        ("Anywhere in the world", set(), False),
        ("Cambridge, MA", set(), False),
        ("Athens, GA", set(), False),
        ("Georgia", set(), False),
        ("Springfield", set(), False),
        ("unknown", set(), False),
        ("", set(), False),
        (None, set(), False),
    ],
)
def test_places_reads_a_country_only_when_it_is_certain(location, countries, complete):
    assert PLACES.resolve(location) == (countries, complete)


def test_region_words_and_groups_expand_to_countries():
    europe, complete = PLACES.resolve("Remote - EMEA")
    assert complete and {"DE", "GB", "IL", "ZA"} <= europe and "US" not in europe
    assert PLACES.expand(["EU", "US"]) >= {"FR", "DE", "US"} and "GB" not in PLACES.expand(["EU"])
    assert PLACES.resolve("Americas")[0] >= {"US", "CA", "BR"}


@pytest.mark.parametrize(
    "location,countries",
    [
        ("Beirut, Beyrouth, Lebanon", {"LB"}),  # the two own addresses in #63
        ("Panamá, Panamá, Panama", {"PA"}),
        ("Amman, Jordan", {"JO"}),
        ("Kathmandu, Nepal", {"NP"}),
        ("Quito, Ecuador", {"EC"}),
        ("Sarajevo, Bosnia and Herzegovina", {"BA"}),
        ("Port of Spain, Trinidad & Tobago", {"TT"}),
        ("Castries, St. Lucia", {"LC"}),
        ("Kingstown, Saint Vincent and the Grenadines", {"VC"}),  # read without its lowercase "the"
        ("Banjul, the Gambia", {"GM"}),
        ("Abidjan, Ivory Coast", {"CI"}),
        ("Yangon, Myanmar (Burma)", {"MM"}),
        ("Mbabane, Swaziland", {"SZ"}),
        ("Praia, Cape Verde", {"CV"}),
        ("Dili, East Timor", {"TL"}),
        ("Kinshasa, DRC", {"CD"}),
        ("Brazzaville, Republic of the Congo", {"CG"}),
        ("Riyadh, KSA", {"SA"}),
        ("Basseterre, St Kitts & Nevis", {"KN"}),
        ("Santo Domingo, República Dominicana", {"DO"}),
        ("Abidjan, Côte d’Ivoire", {"CI"}),  # with a curly apostrophe
        ("Alger, Algérie", {"DZ"}),
        ("Maputo, Moçambique", {"MZ"}),
        ("São Tomé e Príncipe", {"ST"}),
        ("Willemstad, Curacao", {"CW"}),  # Curaçao without its cedilla
        ("Brno, Česko", {"CZ"}),
        ("Debrecen, Magyarország", {"HU"}),
        ("Liège, Belgique", {"BE"}),
        ("Gent, België", {"BE"}),
        ("Lugano, Svizzera", {"CH"}),
        ("Esch-sur-Alzette, Lëtzebuerg", {"LU"}),
        ("Tórshavn, Føroyar", {"FO"}),
        ("Nuuk, Kalaallit Nunaat", {"GL"}),
        ("Tashkent, Oʻzbekiston", {"UZ"}),
        ("Galway, Éire", {"IE"}),
    ],
)
def test_every_country_is_read_by_its_english_alternate_and_local_names(location, countries):
    assert PLACES.resolve(location) == (countries, True)


def test_names_are_compared_without_accents_or_case():
    spellings = {
        "PE": ["Perú", "Peru", "PERÚ"],
        "MX": ["México", "Mexico"],
        "TR": ["Türkiye", "Turkiye", "TÜRKİYE"],  # a Turkish capital İ
        "PA": ["Panamá", "Panama", "PANAMA"],
        "CI": ["Côte d’Ivoire", "Côte d'Ivoire", "Cote d'Ivoire", "COTE D'IVOIRE"],
    }
    assert {name: PLACES.resolve(name) for names in spellings.values() for name in names} == {
        name: ({code}, True) for code, names in spellings.items() for name in names
    }
    # The cities, states and provinces are compared the same way. Ł has no accent to drop: both are listed.
    for one, other, country in (
        ("München", "Munchen", "DE"),
        ("Zürich", "ZURICH", "CH"),
        ("Wrocław", "Wroclaw", "PL"),
        ("Laval, Québec", "Laval, Quebec", "CA"),
        ("Springfield, PENNSYLVANIA", "Springfield, pennsylvania", "US"),
    ):
        assert PLACES.resolve(one) == PLACES.resolve(other) == ({country}, True), one


def test_a_country_named_before_a_state_or_province_is_a_town_there():
    expected = {
        "Lebanon, PA": (set(), False),  # Pennsylvania's or Panama's PA, and no known city to settle it
        "Lebanon, Pennsylvania": ({"US"}, True),
        "Lebanon, Pennsylvania, United States": ({"US"}, True),
        "Peru, IN": (set(), False),
        "Mexico, MO": (set(), False),  # read as Mexico before
        "Holland, MI": ({"US"}, True),  # read as the Netherlands before
        "Jordan, Minnesota": ({"US"}, True),
        "Mexico City, Mexico": ({"MX"}, True),
        "US, CA, Santa Clara": ({"US"}, True),  # a state of the country named does not make it a town
        "United States, Georgia": ({"US"}, True),
        "Toronto, Ontario, Canada": ({"CA"}, True),
        # Known limit: a country's name alone is the country, even where a listing means a US town.
        "Lebanon": ({"LB"}, True),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_georgia_is_settled_only_by_a_known_city():
    expected = {
        "Georgia": (set(), False),  # the US state's name or the country's
        "Atlanta, Georgia": ({"US"}, True),
        "Tbilisi, Georgia": ({"GE"}, True),
        "Rome, Georgia": (set(), False),  # a town in the US state: read as Italy before
        "Tbilisi, GE": ({"GE"}, True),
        "Republic of Georgia": ({"GE"}, True),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_a_new_countrys_code_is_read_only_where_an_address_puts_its_country():
    expected = {
        "Beirut, Beyrouth, LB": ({"LB"}, True),  # locality, region, country
        "Remote - NA": (set(), False),  # North America, not Namibia
        "Mumbai, MH": ({"IN"}, True),  # Maharashtra, not the Marshall Islands: the known city decides
        "Hyderabad, TG, IN": ({"IN"}, True),  # Telangana, not Togo
        "Walldorf, BW, DE, 69190": (set(), False),  # Baden-Württemberg, not Botswana; the postcode is no part
        "Curitiba, PR, Brazil": ({"BR"}, True),  # Paraná, not Puerto Rico
        "Paris, FR": ({"FR"}, True),  # the codes career sites write alone are still read alone
        "Remote, PL": ({"PL"}, True),
        "Congo": (set(), False),  # a name two countries share is no country's
        "Virgin Islands": (set(), False),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_every_canadian_province_and_territory_is_read_by_code_and_name():
    expected = {
        "Saskatoon, SK, CA": ({"CA"}, True),  # read as Slovakia before (#68)
        "Regina, SK, Canada": ({"CA"}, True),  # with Slovakia too, before
        "St. John's, NL, Canada": ({"CA"}, True),
        "Charlottetown, PE, Canada": ({"CA"}, True),
        "Whitehorse, YT, Canada": ({"CA"}, True),
        "Iqaluit, NU, CA": ({"CA"}, True),
        "Yellowknife, NT": ({"CA"}, True),
        "Saskatoon, Saskatchewan": ({"CA"}, True),
        "St. John's, Newfoundland and Labrador": ({"CA"}, True),
        "Charlottetown, Prince Edward Island": ({"CA"}, True),
        "Iqaluit, Nunavut": ({"CA"}, True),
        "Yellowknife, Northwest Territories": ({"CA"}, True),
        "Whitehorse, Yukon": ({"CA"}, True),
        "Halifax, Nouvelle-Écosse": ({"CA"}, True),
        "Charlottetown, Île-du-Prince-Édouard": ({"CA"}, True),
        "St. John's, Terre-Neuve-et-Labrador": ({"CA"}, True),
        "Moncton, Nouveau-Brunswick": ({"CA"}, True),
        "Kelowna, Colombie-Britannique": ({"CA"}, True),
        "Laval, Québec": ({"CA"}, True),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_a_province_code_that_is_also_a_countrys_proves_nothing_alone():
    expected = {
        "Amsterdam, NL": ({"NL"}, True),  # a known city settles it
        "Lima, PE": ({"PE"}, True),
        "Bratislava, SK": ({"SK"}, True),
        "Toronto, SK": ({"CA"}, True),
        "Humboldt, SK": (set(), False),  # read as Slovakia before: a town the table does not know settles nothing
        "Seattle, SK": (set(), False),  # a US city settles no Canadian province
        "NL, PE": (set(), False),  # two such codes settle nothing: CA must be one of them
        "Monterrey, NL, Mexico": ({"MX"}, True),  # Nuevo León's NL: read as the Netherlands too, before
        "Netherlands": ({"NL"}, True),  # the countries by name are read as before
        "Peru": ({"PE"}, True),
        "Slovakia": ({"SK"}, True),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_the_cities_of_the_provinces_whose_codes_are_also_countries_settle_them():
    expected = {
        "Saskatoon, SK": ({"CA"}, True),
        "Regina, SK": ({"CA"}, True),
        "St. John's, NL": ({"CA"}, True),
        "Charlottetown, PE": ({"CA"}, True),
        "Whitehorse, YT": ({"CA"}, True),
        "Iqaluit, NU": ({"CA"}, True),
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def test_the_main_job_cities_of_the_netherlands_peru_and_slovakia_settle_their_codes():
    expected = {
        "Delft, NL": ({"NL"}, True),  # Dutch employers write this form: NL alone proves nothing
        "Leiden, Zuid-Holland, NL": ({"NL"}, True),
        "The Hague, NL": ({"NL"}, True),
        "Den Haag, NL": ({"NL"}, True),
        "'s-Hertogenbosch, NL": ({"NL"}, True),
        "Hoofddorp, NL": ({"NL"}, True),
        "Cusco, PE": ({"PE"}, True),
        "Arequipa, PE": ({"PE"}, True),
        "Košice, SK": ({"SK"}, True),
        "Banska Bystrica, SK": ({"SK"}, True),  # without its accent
        "Breda, IA": ({"US"}, True),  # a state of its own still decides
        "Zwolle, LA": (set(), False),  # and a state code that is also a country's (Laos) is no Dutch one
    }
    assert {location: PLACES.resolve(location) for location in expected} == expected


def unique_keys(pairs):
    keys = [key for key, _ in pairs]
    assert len(keys) == len(set(keys)), sorted({key for key in keys if keys.count(key) > 1})
    return dict(pairs)


def test_the_table_has_every_iso_3166_country_once_with_a_known_region():
    data = json.loads(TABLE.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    countries = data["countries"]
    assert len(countries) == 249 and set(countries) == ISO_3166_1
    assert [code for code in countries if not re.fullmatch(r"[A-Z]{2}", code)] == []
    tags = {tag for tags in data["region_words"].values() for tag in tags}
    assert {code: entry["region"] for code, entry in countries.items() if entry["region"] not in tags} == {}
    assert [code for code, entry in countries.items() if not entry["names"]] == []
    regions = C.normalize(dict(MINIMAL, locations={"regions": ["LB", "PA", "GE"]}), PLACES)["locations"]["regions"]
    assert regions == ["LB", "PA", "GE"]  # any country's code is a region criteria accept


def test_no_name_reads_as_two_countries():
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    owners: dict[str, set[str]] = {}
    for code, entry in data["countries"].items():
        for name in entry["names"]:
            for key in C._keys(name):  # as the reader compares it: folded, with and without noise words
                owners.setdefault(key, set()).add(code)
    assert {key: codes for key, codes in owners.items() if len(codes) > 1} == {}
    cities = {key: code for code, names in data["cities"].items() for name in names for key in C._keys(name)}
    assert {
        key: (owners[key], cities[key]) for key in owners.keys() & cities.keys() if owners[key] != {cities[key]}
    } == {}
    towns: dict[str, set[str]] = {}  # nor a city under two countries
    for code, names in data["cities"].items():
        for name in names:
            for key in C._keys(name):
                towns.setdefault(key, set()).add(code)
    assert {key: codes for key, codes in towns.items() if len(codes) > 1} == {}


def test_the_code_lists_agree_with_the_countries():
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    countries, ambiguous = set(data["countries"]), set(data["ambiguous_state_codes"])
    # Every US state or Canadian province code that is also a country's, and no other code.
    assert ambiguous == (set(data["us_states"]) | set(data["ca_provinces"])) & countries
    assert not set(data["us_states"]) & set(data["ca_provinces"])  # each such code has one home
    provinces = data["ca_provinces"]
    assert len(provinces) == 13 and all(names and isinstance(names, list) for names in provinces.values())
    alone = set(data["codes_read_alone"])
    assert alone <= countries and not alone & ambiguous
    assert set(data["ambiguous_state_names"]) <= set(data["us_states"].values())
    groups = data["groups"]
    assert {name: codes for name, codes in groups.items() if not set(codes) <= countries} == {}
    assert [name for name in groups if name in countries and name not in groups[name]] == []  # US keeps the US


def stage0(**criteria):
    return C.Stage0(C.normalize(dict(MINIMAL, **criteria), PLACES), PLACES)


def test_stage0_drops_only_on_positive_evidence():
    check = stage0(companies={"exclude": ["Acme, Inc."]}, employment=["full_time"], locations={"regions": ["US"]}).check
    assert check(job()) == "company"
    assert check(job(company="Globex")) is None
    assert check(job(company="Globex", employment_type="Internship")) == "employment"
    assert check(job(company="Globex", employment_type="unknown")) is None  # unknown passes
    assert check(job(company="Globex", location="Berlin, Germany")) == "location"
    assert check(job(company="Globex", location="Berlin / New York")) is None  # one allowed place is enough
    assert check(job(company="Globex", location="Remote")) is None  # unreadable passes
    assert check(job(company="Globex", location="Germany; Remote")) is None  # partly unreadable passes
    assert check(job(company="Globex", location="Remote - Europe")) == "location"


def test_no_constraints_means_nothing_is_dropped():
    check = stage0().check
    assert check(job(location="Berlin, Germany", employment_type="Internship")) is None


def test_an_own_address_in_panama_or_lebanon_is_outside_us_regions():
    outside = stage0(locations={"regions": ["US"]}).outside  # what parks a posting by its own record (#54)
    assert outside("Panamá, Panamá, Panama") == ["PA"] and outside("Beirut, Beyrouth, Lebanon") == ["LB"]
    assert outside("Lebanon, PA") == [] and outside("Lebanon, Pennsylvania, United States") == []


def test_a_place_in_saskatchewan_is_in_canada_for_stage_0():
    canada, slovakia = stage0(locations={"regions": ["CA"]}), stage0(locations={"regions": ["SK"]})
    assert canada.check(job(location="Saskatoon, SK, CA")) is None  # dropped as Slovakia before (#68)
    assert canada.outside("Regina, SK, Canada") == [] and slovakia.outside("Regina, SK, Canada") == ["CA"]


def test_the_us_region_takes_in_the_us_territories():
    us, canada = stage0(locations={"regions": ["US"]}), stage0(locations={"regions": ["CA"]})
    for place, territory in (("San Juan, Puerto Rico", "PR"), ("Hagåtña, Guam", "GU")):
        assert PLACES.resolve(place) == ({territory}, True)  # still its own code, as a place tag would say
        assert us.check(job(location=place)) is None and us.outside(place) == []  # neither dropped nor parked
        assert canada.outside(place) == [territory]
    assert PLACES.expand(["US"]) == {"US", "PR", "VI", "GU", "AS", "MP", "UM"}
    assert PLACES.expand(["EU"]) == set(
        "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
    )
    assert C.normalize(dict(MINIMAL, locations={"regions": ["US", "PR"]}), PLACES)["locations"]["regions"] == [
        "US",
        "PR",
    ]


def test_profile_hashes_change_only_with_what_each_stage_reads():
    base = C.normalize(MINIMAL, PLACES)
    hashes = C.profile_hashes(base, "Five years of mixed-methods research.")
    same = dict(
        base,
        thresholds={"strong": 90, "good": 70, "publish_min": 10},
        caps={"max_triage": 5, "max_assess": 5, "max_attempts": 1},
        companies={"prefer": ["Acme"], "exclude": ["Globex"]},
        backfill={"days": None},
    )
    assert C.profile_hashes(same, "Five years  of\nmixed-methods research.") == hashes
    retargeted = C.profile_hashes(
        dict(base, targets={"roles": ["Product Analyst"], "adjacent": [], "avoid": []}),
        "Five years of mixed-methods research.",
    )
    assert retargeted["triage"] != hashes["triage"] and retargeted["assess"] != hashes["assess"]
    rewritten = C.profile_hashes(base, "Ten years of quantitative research.")
    assert rewritten["triage"] == hashes["triage"] and rewritten["assess"] != hashes["assess"]


def test_a_title_that_names_an_internship_counts_as_evidence():
    check = stage0(employment=["full_time"]).check
    assert check(job(title="PhD Intern, Data Science (2027)")) == "employment"
    assert check(job(title="Data Science Internship")) == "employment"
    assert (
        check(job(title="Internal Tools Researcher")) is None
        and check(job(title="International UX Researcher")) is None
    )
    assert stage0(employment=["full_time", "internship"]).check(job(title="Research Intern")) is None
    assert stage0().check(job(title="Research Intern")) is None  # no employment constraint, nothing to drop


def test_a_monthly_budget_is_optional_and_judges_nothing_again():
    for good in (None, 50, 12.5):
        assert C.normalize(dict(MINIMAL, caps={"max_usd_per_month": good}), PLACES)["caps"]["max_usd_per_month"] == good
    for bad in (0, -5, "50", True):
        with pytest.raises(C.CriteriaError, match="max_usd_per_month"):
            C.normalize(dict(MINIMAL, caps={"max_usd_per_month": bad}), PLACES)
    plain = C.normalize(MINIMAL, PLACES)
    budgeted = C.normalize(dict(MINIMAL, caps={"max_usd_per_month": 50}), PLACES)
    assert C.profile_hashes(plain, "Five years of research.") == C.profile_hashes(budgeted, "Five years of research.")


def test_listing_limits_are_validated_and_judge_nothing_again():
    out = C.normalize(MINIMAL, PLACES)["thresholds"]
    assert (out["direct_min"], out["max_required_years"], out["max_seniority"], out["unmet_requirements"]) == (
        None,
        None,
        None,
        [],
    )
    good = {"direct_min": 30, "max_required_years": 3, "max_seniority": "mid", "unmet_requirements": ["degree"]}
    assert C.normalize(dict(MINIMAL, thresholds=good), PLACES)["thresholds"]["max_seniority"] == "mid"
    for bad, problem in (
        ({"direct_min": 101}, "direct_min"),
        ({"max_required_years": -1}, "max_required_years"),
        ({"max_required_years": 2.5}, "max_required_years"),
        ({"max_seniority": "lead"}, "max_seniority"),
        ({"unmet_requirements": ["degree", "visa"]}, "unmet_requirements"),
        ({"unmet_requirements": "degree"}, "unmet_requirements"),
    ):
        with pytest.raises(C.CriteriaError, match=problem):
            C.normalize(dict(MINIMAL, thresholds=bad), PLACES)
    plain = C.normalize(MINIMAL, PLACES)
    limited = C.normalize(dict(MINIMAL, thresholds=good), PLACES)
    assert C.profile_hashes(plain, "Five years of research.") == C.profile_hashes(limited, "Five years of research.")
