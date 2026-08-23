"""Category metadata and cached Mongo-backed video counts.

The hand-curated table is extended from newer ``textParams`` markers: ``!!!``
opens a major, ``+++`` opens a sub, and an unprefixed hashtag marker closes
the matching sub or the major. Hashtag overlap, not begin/end words, decides.
Base-table subs must be closed; only the last major may be open — open subs are
always auto-discovered from markers, which is what gives them their hashtags.
"""

import asyncio
from bisect import bisect_left, bisect_right
import copy
import logging
import re
import time

from config import CATEGORY_COUNT_RETRY_TTL, CATEGORY_COUNT_TTL, DB_NAME

logger = logging.getLogger(__name__)

STUFF_CHANNEL = "-1001706757504"

CATEGORY_TABLE = [
{"name":"OLD (Kink old)","tag":"OLD","start":3,"end":90,"subs":[{"name":"SS","start":33,"end":47},{"name":"Insex / SB","start":47,"end":52},{"name":"Naughty America","start":52,"end":53},{"name":"2cst","start":53,"end":60},{"name":"mshf","start":60,"end":65},{"name":"tbff","start":65,"end":81},{"name":"Braz old / Other old","start":81,"end":90}]},
{"name":"Ultimate Surrender OLD","tag":"US_OLD","start":90,"end":937,"subs":[{"name":"US old","start":90,"end":369},{"name":"Evolved old","start":370,"end":415},{"name":"US old added","start":415,"end":870},{"name":"Evolved old added","start":870,"end":937}]},
{"name":"SexyFightingZone","tag":"SFZ","start":938,"end":1299,"subs":[]},
{"name":"CWC","tag":"CWC","start":1299,"end":1326,"subs":[{"name":"CWC","start":1299,"end":1316},{"name":"APL","start":1316,"end":1326}]},
{"name":"TribDolls","tag":"TD","start":1326,"end":1743,"subs":[]},
{"name":"Fighting Dolls","tag":"FD","start":1744,"end":2564,"subs":[]},
{"name":"Foxy Combat","tag":"FC","start":2565,"end":2924,"subs":[]},
{"name":"Primal (unlabeled)","tag":"PRIMAL","start":2925,"end":3123,"subs":[]},
{"name":"Defeated block","tag":"DEFS","start":3124,"end":3589,"subs":[{"name":"DefeatedSexfight","start":3124,"end":3282},{"name":"DefeatedX","start":3283,"end":3469},{"name":"XDefeatedX","start":3470,"end":3551},{"name":"FemX","start":3552,"end":3571},{"name":"Unsorted","start":3571,"end":3588}]},
{"name":"Brazzers","tag":"BRAZ","start":3589,"end":8272,"subs":[{"name":"BBLIB","start":3590,"end":3683},{"name":"BTAS","start":3683,"end":3990},{"name":"BTAW","start":3991,"end":4259},{"name":"BTIS","start":4260,"end":4345},{"name":"BTIU","start":4346,"end":4379},{"name":"BWB","start":4380,"end":4514},{"name":"BZV","start":4515,"end":4574},{"name":"CFNM","start":4575,"end":4591},{"name":"DA","start":4591,"end":4821},{"name":"BTIS2","start":4822,"end":4911},{"name":"BTIU2","start":4912,"end":4977},{"name":"DM","start":4978,"end":5172},{"name":"DWAP","start":5173,"end":5246},{"name":"FLIXXX","start":5247,"end":5252},{"name":"HCBA","start":5253,"end":5262},{"name":"MGB","start":5263,"end":5359},{"name":"MLIB","start":5360,"end":5520},{"name":"PLIB","start":5521,"end":5822},{"name":"RWS","start":5823,"end":6234},{"name":"SGS","start":6235,"end":6251},{"name":"SS","start":6252,"end":6257},{"name":"WGP","start":6258,"end":6269},{"name":"ZZ","start":6270,"end":6366},{"name":"BGB","start":6367,"end":6633},{"name":"MIC","start":6634,"end":6735},{"name":"HAM","start":6736,"end":7361},{"name":"TLIB","start":7362,"end":7869},{"name":"BEX","start":7876,"end":8272}]},
{"name":"TeamSkeet","tag":"TS","start":8273,"end":8857,"subs":[{"name":"Dyked","start":8274,"end":8374},{"name":"PunishTeens","start":8375,"end":8524},{"name":"MG","start":8528,"end":8571},{"name":"BFF","start":8572,"end":8776},{"name":"Other","start":8777,"end":8855},{"name":"Other2 / MG2","start":9395,"end":9403}]},
{"name":"Evil Angel","tag":"EA","start":8859,"end":9394,"subs":[]},
{"name":"Reality Kings","tag":"RK","start":9404,"end":10093,"subs":[{"name":"WeLiveTogether","start":9405,"end":9917},{"name":"MoneyTalks","start":9918,"end":10093}]},
{"name":"OnlyFans","tag":"OF","start":10094,"end":10139,"subs":[{"name":"BrittFit","start":10095,"end":10139}]},
{"name":"Kink","tag":"KINK","start":10140,"end":16680,"subs":[{"name":"Ultimate Surrender new","start":10141,"end":11041},{"name":"FuckingMachines","start":11042,"end":11696},{"name":"SAS","start":11697,"end":12527},{"name":"WhippedAss","start":12528,"end":13444},{"name":"Hogtied","start":13445,"end":14430},{"name":"DeviceBondage","start":14431,"end":15280},{"name":"Dungeon Sex","start":15282,"end":15396},{"name":"Public Disgrace","start":15398,"end":15572},{"name":"Families Tied","start":15573,"end":15650},{"name":"Kink Features","start":15652,"end":15726},{"name":"Wired Pussy","start":15727,"end":15999},{"name":"Training of O","start":16025,"end":16305},{"name":"BoundGangBangs","start":16306,"end":16618},{"name":"ElectroSluts","start":16619,"end":16679}]},
{"name":"PornPros","tag":"PORNPROS","start":16685,"end":18061,"subs":[{"name":"TBFF","start":16686,"end":16917},{"name":"18YearsOld","start":16918,"end":17208},{"name":"CockCompetition","start":17209,"end":17266},{"name":"CrueltyParty","start":17278,"end":17342},{"name":"Disgraced18 (partial)","start":17343,"end":17366},{"name":"DTL","start":17367,"end":17470},{"name":"FlexiblePositions","start":17471,"end":17491},{"name":"MassageCreep","start":17492,"end":17619},{"name":"RealExGF","start":17620,"end":17866},{"name":"ShadyPI","start":17867,"end":17879},{"name":"PornPros default","start":17880,"end":18060}]},
{"name":"BangBros","tag":"BANGBROS","start":18061,"end":23508,"subs":[{"name":"PartyOfThree","start":18061,"end":18194},{"name":"PowerMunch","start":18195,"end":18239},{"name":"StepMomVideos / MilfSoup","start":18249,"end":18311},{"name":"BangBros18","start":18312,"end":18567},{"name":"Remastered","start":18579,"end":18769},{"name":"AssParade","start":18770,"end":19432},{"name":"BangBus","start":19433,"end":20012},{"name":"FacialFest","start":20013,"end":20868},{"name":"BigTitsRoundAsses","start":20869,"end":21215},{"name":"TryOuts","start":21216,"end":21228},{"name":"DormInvasion","start":21229,"end":21266},{"name":"DirtyWorldTour","start":21267,"end":21276},{"name":"MonsterCock","start":21277,"end":21767},{"name":"MomIsHorny","start":21768,"end":21792},{"name":"StreetRanger","start":21793,"end":21811},{"name":"SluttyWhiteGirl","start":21812,"end":21821},{"name":"BangCasting","start":21822,"end":21858},{"name":"CanHeScore","start":21859,"end":21949},{"name":"BangBrosCasting","start":21950,"end":22239},{"name":"POV","start":22240,"end":22421},{"name":"BigTitCreamPie","start":22422,"end":22713},{"name":"ColombiaFuckFest","start":22714,"end":22743},{"name":"Chongas","start":22744,"end":22760},{"name":"FuckTeamFive","start":22761,"end":22903},{"name":"GloryHoleLoads","start":22904,"end":22940},{"name":"MrAnal","start":22941,"end":22981},{"name":"MyDirtyMaid","start":22982,"end":23086},{"name":"MILF","start":23087,"end":23120},{"name":"PAWG","start":23121,"end":23241},{"name":"PublicBang","start":23241,"end":23361},{"name":"PornStarMassage","start":23362,"end":23433},{"name":"Other","start":23434,"end":23507}]},
{"name":"SexyFightingZone 2","tag":"SFZ2","start":23509,"end":23602,"subs":[]},
{"name":"Disgraced18 (real)","tag":"D18","start":23603,"end":23704,"subs":[]},
{"name":"Vixen","tag":"VIXEN","start":23705,"end":23905,"subs":[]},
{"name":"SmotheredSlave","tag":"SMOTHERED","start":23906,"end":24070,"subs":[]},
{"name":"Dillion Harper","tag":"DILLION","start":24071,"end":24139,"subs":[]},
{"name":"Faye Reagan","tag":"FAYE","start":24141,"end":24146,"subs":[]},
{"name":"SexyFightingZone 3","tag":"SFZ3","start":24147,"end":24218,"subs":[]},
{"name":"Lexi Belle","tag":"LEXI","start":24219,"end":24555,"subs":[]},
{"name":"Lily Labeau","tag":"LILY","start":24556,"end":24627,"subs":[]},
{"name":"PornstarPunishment","tag":"PSP","start":24629,"end":24699,"subs":[]},
{"name":"HotAndMean (new)","tag":"HAM2","start":24700,"end":24765,"subs":[]},
{"name":"Primal 2","tag":"PRIMAL2","start":24767,"end":25745,"subs":[{"name":"Grapple","start":24768,"end":24793},{"name":"Wrestling","start":24794,"end":25028},{"name":"Cosplay / Superheroines","start":25029,"end":25054},{"name":"Other","start":25055,"end":25745}]},
{"name":"Brazil BFF","tag":"BRAZILBFF","start":25747,"end":26324,"subs":[]},
{"name":"DefeatedSexfight 2","tag":"DEFS2","start":26325,"end":26549,"subs":[]},
{"name":"DefeatedX 2","tag":"DEFX2","start":26550,"end":27097,"subs":[]},
{"name":"XFights","tag":"XFIGHTS","start":27098,"end":31295,"subs":[{"name":"Academy Wrestling","start":27099,"end":27143},{"name":"Antscha","start":27144,"end":27173},{"name":"APL","start":27174,"end":27200},{"name":"Brazil","start":27201,"end":27281},{"name":"CatzReview","start":27282,"end":27306},{"name":"CJ Films","start":27307,"end":27326},{"name":"CPL","start":27327,"end":27601},{"name":"Defeated","start":27602,"end":27697},{"name":"DT Sexfight","start":27698,"end":27733},{"name":"Eros","start":27734,"end":27763},{"name":"Evolved Fights","start":27764,"end":27910},{"name":"Foxy Combat","start":27911,"end":27994},{"name":"Fighting Dolls","start":27995,"end":28775},{"name":"Female Combat Stars","start":28776,"end":28823},{"name":"Femwin","start":28824,"end":28844},{"name":"Festelle","start":28845,"end":28903},{"name":"GirlsFightClub","start":28904,"end":28953},{"name":"Japanese","start":28954,"end":28967},{"name":"Kontex","start":28968,"end":29049},{"name":"Korean","start":29050,"end":29136},{"name":"Mexican","start":29137,"end":29159},{"name":"Mr Rain","start":29160,"end":29254},{"name":"NudeFightClub","start":29255,"end":29272},{"name":"Pompeii","start":29273,"end":29286},{"name":"Real Catfights","start":29287,"end":29392},{"name":"RVQ Brazil","start":29393,"end":29530},{"name":"SFZ","start":29531,"end":30135},{"name":"Sisterhood of Sin","start":30136,"end":30204},{"name":"SuiteFights","start":30205,"end":30278},{"name":"TribDolls","start":30279,"end":30669},{"name":"TillyTown","start":30670,"end":30698},{"name":"WeBringIt","start":30699,"end":30744},{"name":"Other","start":30745,"end":31294}]},
{"name":"AdultTime","tag":"ADULTTIME","start":31302,"end":35335,"subs":[{"name":"21Sextury","start":31303,"end":31758},{"name":"21Sextury / NudeFightClub","start":31304,"end":31413},{"name":"21Sextury / TeenBitchClub","start":31414,"end":31717},{"name":"21Sextury / Other","start":31718,"end":31757},{"name":"Burning Angel","start":31759,"end":31976},{"name":"Devils Film","start":31977,"end":32239},{"name":"Fame","start":32240,"end":32405},{"name":"GirlsWay","start":32406,"end":34368},{"name":"GirlsWay / MommysGirl","start":32407,"end":33014},{"name":"GirlsWay / SexTapeLesbians","start":33015,"end":33057},{"name":"GirlsWay / WebYoung","start":33058,"end":33421},{"name":"GirlsWay / WeLikeGirls","start":33422,"end":33446},{"name":"GirlsWay / Other","start":33447,"end":34367},{"name":"LesbianX","start":34369,"end":34448},{"name":"LezBeBad","start":34449,"end":34604},{"name":"AdultTime Other","start":34605,"end":34647},{"name":"PureTaboo","start":34648,"end":34657},{"name":"Slayed","start":34658,"end":34704},{"name":"Vivid 19","start":34705,"end":35321},{"name":"Vivid Other","start":35322,"end":35334}]},
{"name":"DirtyWrestlingPit","tag":"DWP","start":35336,"end":35913,"subs":[]},
{"name":"Reality Kings 2","tag":"RK2","start":35916,"end":None,"subs":[{"name":"WeLiveTogether 2","start":35917,"end":36596},{"name":"MomsBangTeens","start":36597,"end":36849},{"name":"MomsLickTeens","start":36850,"end":37051},{"name":"CumFiesta","start":37052,"end":37523},{"name":"HDLove","start":37524,"end":37574},{"name":"Pure18","start":37575,"end":37847},{"name":"FirstTimeAuditions","start":37848,"end":38134},{"name":"InTheVIP","start":38135,"end":38236},{"name":"BigTitBoss","start":38237,"end":38346},{"name":"CFNM","start":38347,"end":38388},{"name":"HotGirlsGame","start":38389,"end":38468},{"name":"MikeInBrazil","start":38469,"end":38655}]}
]

_count_cache: tuple[bool, dict[str, int]] | None = None
_count_cache_expires: float = 0.0
_refresh_lock = asyncio.Lock()
_table_state: tuple[list[dict], dict[str, tuple[int, int | None]]]


def get_collection():
    """Return the patchable source collection accessor."""
    from db import _client

    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[DB_NAME]["postData1"]


async def get_categories() -> dict:
    """Return category metadata with cached video counts."""
    counts_exact, counts = await _load_counts()
    return {
        "counts_exact": counts_exact,
        "categories": _build_categories(counts),
    }


async def ensure_fresh() -> None:
    """Refresh the marker-extended table when the cache is missing or expired."""
    await _load_counts()


def resolve(key: str) -> tuple[int, int | None] | None:
    """Resolve a category key to its exclusive marker bounds."""
    return _table_state[1].get(key)


async def _load_counts() -> tuple[bool, dict[str, int]]:
    if _is_cache_fresh():
        return _count_cache
    async with _refresh_lock:
        # Callers that queued behind an in-flight refresh reuse its result.
        if _is_cache_fresh():
            return _count_cache
        await _refresh_counts()
    return _count_cache


def _is_cache_fresh() -> bool:
    return _count_cache is not None and time.monotonic() < _count_cache_expires


async def _refresh_counts() -> None:
    global _count_cache, _count_cache_expires, _table_state
    try:
        message_ids, marker_documents = await _fetch_refresh_data()
        table, ranges = _extend_table(marker_documents)
        _table_state = table, ranges
        _count_cache = True, _count_ranges(message_ids)
        _count_cache_expires = time.monotonic() + CATEGORY_COUNT_TTL
    except Exception:
        logger.exception("Failed to load category counts from postData1")
        # A refresh failure must not downgrade previously loaded exact counts.
        if _count_cache is None:
            _count_cache = False, _estimate_counts()
        _count_cache_expires = time.monotonic() + CATEGORY_COUNT_RETRY_TTL


async def _fetch_refresh_data() -> tuple[list[int], list[dict]]:
    collection = get_collection()
    marker_cursor = collection.find(
        {
            "paramType": "textParams",
            "forwardFromMessageId": {"$gt": HIGHEST_HARDCODED_ID},
        },
        {"_id": 0, "forwardFromMessageId": 1, "text": 1},
    ).sort("forwardFromMessageId", 1)
    marker_documents = await marker_cursor.to_list(None)
    cursor = collection.find(
        {"paramType": "vidParams"},
        {"_id": 0, "forwardFromMessageId": 1},
    )
    documents = await cursor.to_list(None)
    message_ids = []
    for document in documents:
        message_id = document.get("forwardFromMessageId")
        if isinstance(message_id, int):
            message_ids.append(message_id)
    message_ids.sort()
    return message_ids, marker_documents


def _count_ranges(message_ids: list[int]) -> dict[str, int]:
    counts = {}
    for key, bounds in _table_state[1].items():
        start, end = bounds
        if end is None:
            counts[key] = len(message_ids) - bisect_right(message_ids, start)
            continue
        counts[key] = bisect_left(message_ids, end) - bisect_right(message_ids, start)
    return counts


def _estimate_counts() -> dict[str, int]:
    counts = {}
    for key, bounds in _table_state[1].items():
        start, end = bounds
        counts[key] = 0 if end is None else end - start - 1
    return counts


def _build_categories(counts: dict[str, int]) -> list[dict]:
    result = []
    for category in _table_state[0]:
        item = {key: value for key, value in category.items() if key != "subs"}
        item["end"] = _serialize_end(item["end"])
        item["count"] = counts[category["key"]]
        item["subs"] = []
        for sub in category["subs"]:
            sub_item = sub.copy()
            sub_item["end"] = _serialize_end(sub_item["end"])
            sub_item["count"] = counts[sub["key"]]
            item["subs"].append(sub_item)
        result.append(item)
    return result


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _serialize_end(end: int | None) -> int | str:
    return "?" if end is None else end


def _prepare_table(table: list[dict]) -> dict[str, tuple[int, int | None]]:
    ranges = {}
    for category in table:
        major_key = _slug(category["tag"])
        category["key"] = major_key
        ranges[major_key] = category["start"], category["end"]
        _prepare_subs(category, major_key, ranges)
    return ranges


def _prepare_subs(category: dict, major_key: str, ranges: dict) -> None:
    sub_keys = {}
    for sub in category["subs"]:
        if not isinstance(sub["end"], int):
            raise ValueError(
                f"Base-table subs must have an integer end: {sub['name']}"
            )
        sub_keys[sub["name"]] = f"{major_key}-{_slug(sub['name'])}"
    for sub in category["subs"]:
        original_name = sub["name"]
        sub["key"] = sub_keys[original_name]
        sub["parent"] = None
        if " / " in original_name:
            parent_name, display_name = original_name.split(" / ", 1)
            sub["parent"] = sub_keys.get(parent_name)
            sub["name"] = display_name
        ranges[sub["key"]] = sub["start"], sub["end"]


def _find_highest_hardcoded_id() -> int:
    highest = 0
    for category in CATEGORY_TABLE:
        for value in (category["start"], category["end"]):
            if isinstance(value, int):
                highest = max(highest, value)
        for sub in category["subs"]:
            for value in (sub["start"], sub["end"]):
                if isinstance(value, int):
                    highest = max(highest, value)
    return highest


def _extend_table(marker_documents: list[dict]) -> tuple[list[dict], dict]:
    table = copy.deepcopy(CATEGORY_TABLE)
    state = _seed_marker_state(table)
    for document in marker_documents:
        marker_id = document.get("forwardFromMessageId")
        text = document.get("text")
        if not isinstance(marker_id, int) or marker_id <= HIGHEST_HARDCODED_ID:
            continue
        if not isinstance(text, str):
            continue
        hashtags = _extract_hashtags(text)
        _apply_marker(table, state, marker_id, text, hashtags)
    return table, _build_ranges(table)


def _seed_marker_state(table: list[dict]) -> dict:
    open_major = table[-1] if table and table[-1]["end"] is None else None
    return {
        "major": open_major,
        "sub": None,
        "tags": set(),
        "keys": _collect_keys(table),
    }


def _apply_marker(
    table: list[dict], state: dict, marker_id: int, text: str, hashtags: list[str]
) -> None:
    if text.startswith("!!!") and hashtags:
        _close_section(state["sub"], marker_id)
        _close_section(state["major"], marker_id)
        state["major"] = _append_major(table, hashtags[0], marker_id, state["keys"])
        state["sub"], state["tags"] = None, set()
        return
    if text.startswith("+++") and hashtags and state["major"] is not None:
        _close_section(state["sub"], marker_id)
        state["sub"] = _append_sub(
            state["major"], hashtags[0], marker_id, state["keys"]
        )
        state["tags"] = {tag.lower() for tag in hashtags}
        return
    if not hashtags:
        return
    marker_tags = {tag.lower() for tag in hashtags}
    if state["sub"] is not None and marker_tags.intersection(state["tags"]):
        _close_section(state["sub"], marker_id)
        state["sub"], state["tags"] = None, set()
        return
    _close_section(state["sub"], marker_id)
    _close_section(state["major"], marker_id)
    state["major"], state["sub"], state["tags"] = None, None, set()


def _collect_keys(table: list[dict]) -> set[str]:
    keys = set()
    for category in table:
        keys.add(category["key"])
        for sub in category["subs"]:
            keys.add(sub["key"])
    return keys


def _extract_hashtags(text: str) -> list[str]:
    return re.findall(r"#([A-Za-z0-9_]+)", text)


def _append_major(table: list[dict], name: str, start: int, used: set[str]) -> dict:
    key = _make_unique_key(_slug(name), used)
    category = {"name": name, "tag": name, "start": start, "end": None,
                "subs": [], "key": key}
    table.append(category)
    return category


def _append_sub(category: dict, name: str, start: int, used: set[str]) -> dict:
    key = _make_unique_key(f"{category['key']}-{_slug(name)}", used)
    sub = {"name": name, "start": start, "end": None,
           "key": key, "parent": None}
    category["subs"].append(sub)
    return sub


def _make_unique_key(candidate: str, used: set[str]) -> str:
    key = candidate
    suffix = 2
    while key in used:
        key = f"{candidate}-{suffix}"
        suffix += 1
    used.add(key)
    return key


def _close_section(section: dict | None, marker_id: int) -> None:
    if section is not None and section["end"] is None:
        section["end"] = marker_id


def _build_ranges(table: list[dict]) -> dict[str, tuple[int, int | None]]:
    ranges = {}
    for category in table:
        ranges[category["key"]] = category["start"], category["end"]
        for sub in category["subs"]:
            ranges[sub["key"]] = sub["start"], sub["end"]
    return ranges


_base_ranges = _prepare_table(CATEGORY_TABLE)
HIGHEST_HARDCODED_ID = _find_highest_hardcoded_id()
_table_state = CATEGORY_TABLE, _base_ranges
