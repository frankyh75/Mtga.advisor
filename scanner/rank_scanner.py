"""Rank & Account Memory Scanner for MTGA.

Reads player rank data and account info from MTGA's IL2CPP memory by
navigating the WrapperController → PlayerRankServiceWrapper → _combinedRankInfo
and WrapperController → AccountClient → AccountInformation field chains.

This mirrors what mtgatool's queries.rs ``ranks_from()`` and ``account_from()``
do for the Mono backend, but uses the existing IL2CPP navigation infrastructure
from il2cpp_nav.py.

Memory layout (from queries.rs):

  Ranks:
    WrapperController.Instance
      → <PlayerRankServiceWrapper>k__BackingField
        → _combinedRankInfo
          → constructedClass (u32: 0=None, 1=Spark, ..., 8=Mythic)
          → constructedLevel, constructedStep
          → constructedMatchesWon, constructedMatchesLost, constructedMatchesDrawn
          → constructedSeasonOrdinal, constructedPercentile, constructedLeaderboardPlace
          → limitedClass, limitedLevel, limitedStep, ...
          → playerId (string)

  Account:
    WrapperController.Instance
      → <AccountClient>k__BackingField
        → <AccountInformation>k__BackingField
          → DisplayName, AccountID, PersonaID, GameID (strings)
          → Email, ExternalID, CountryCode, AccessToken (strings)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from .il2cpp_nav import (
    Il2CppReader,
    MemoryReader,
    MockMemory,
    PymemMemoryAdapter,
    discover_data_segment_base,
    discover_wrapper_controller_via_backref,
    _read_ptr,
    _read_i32,
    _read_u32,
    _read_string_ascii,
    find_class_by_name,
    find_papa_instance,
    get_class_fields,
    read_il2cpp_string,
)

# ---------------------------------------------------------------------------
# Rank class enum (Wizards.Mtga.FrontDoorModels.RankingClassType)
# ---------------------------------------------------------------------------

RANK_CLASS_NAMES: dict[int, str] = {
    0: "None",
    1: "Spark",
    2: "Bronze",
    3: "Silver",
    4: "Gold",
    5: "Platinum",
    6: "Diamond",
    7: "Master",
    8: "Mythic",
}


def rank_class_name(class_value: int) -> str:
    """Convert a RankingClassType integer to its human-readable name."""
    return RANK_CLASS_NAMES.get(class_value, "Unknown")


# ---------------------------------------------------------------------------
# Field name constants — exact C# backing field names in MTGA's IL2CPP
# ---------------------------------------------------------------------------

# WrapperController → PlayerRankServiceWrapper
RANK_WRAPPER_FIELD = "<PlayerRankServiceWrapper>k__BackingField"
# PlayerRankServiceWrapper → _combinedRankInfo
COMBINED_RANK_INFO_FIELD = "_combinedRankInfo"

# WrapperController → AccountClient
ACCOUNT_CLIENT_FIELD = "<AccountClient>k__BackingField"
# AccountClient → AccountInformation
ACCOUNT_INFO_FIELD = "<AccountInformation>k__BackingField"

# Rank info field name prefixes
RANK_PREFIXES = ("constructed", "limited")

# Common field name suffixes
SEASON_ORDINAL_SUFFIX = "SeasonOrdinal"
CLASS_SUFFIX = "Class"
LEVEL_SUFFIX = "Level"
STEP_SUFFIX = "Step"
MATCHES_WON_SUFFIX = "MatchesWon"
MATCHES_LOST_SUFFIX = "MatchesLost"
MATCHES_DRAWN_SUFFIX = "MatchesDrawn"
PERCENTILE_SUFFIX = "Percentile"
LEADERBOARD_PLACE_SUFFIX = "LeaderboardPlace"

# Account info field names
ACCOUNT_STRING_FIELDS = (
    "DisplayName",
    "AccountID",
    "PersonaID",
    "GameID",
    "Email",
    "ExternalID",
    "CountryCode",
    "AccessToken",
)

# PlayerId field in _combinedRankInfo
PLAYER_ID_FIELD = "playerId"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RankInfo:
    """Rank data for a single queue type (constructed or limited)."""
    season_ordinal: int = 0
    class_value: int = 0
    class_name: str = "None"
    level: int = 0
    step: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    percentile: str = ""
    leaderboard_place: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seasonOrdinal": self.season_ordinal,
            "class": self.class_name,
            "classValue": self.class_value,
            "level": self.level,
            "step": self.step,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "percentile": self.percentile,
            "leaderboardPlace": self.leaderboard_place,
        }


@dataclass
class RankScanResult:
    """Result of scanning rank data from memory."""
    player_id: str = ""
    constructed: RankInfo = field(default_factory=RankInfo)
    limited: RankInfo = field(default_factory=RankInfo)
    warnings: list[str] = field(default_factory=list)
    raw_address: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "playerId": self.player_id,
            "constructed": self.constructed.to_dict(),
            "limited": self.limited.to_dict(),
        }


@dataclass
class AccountInfo:
    """Account information scanned from memory."""
    display_name: str = ""
    account_id: str = ""
    persona_id: str = ""
    game_id: str = ""
    email: str = ""
    external_id: str = ""
    country_code: str = ""
    access_token: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Don't expose access_token in to_dict for security
        return {
            "displayName": self.display_name,
            "accountId": self.account_id,
            "personaId": self.persona_id,
            "gameId": self.game_id,
            "email": self.email,
            "externalId": self.external_id,
            "countryCode": self.country_code,
        }


# ---------------------------------------------------------------------------
# Rank scanning — navigate to _combinedRankInfo and read fields
# ---------------------------------------------------------------------------

def scan_ranks_from_instance(
    mem: MemoryReader,
    wrapper_instance: int,
    wrapper_class: int,
) -> RankScanResult:
    """Read rank data from a WrapperController instance.

    Navigates: WrapperController → PlayerRankServiceWrapper → _combinedRankInfo
    Then reads constructed and limited rank fields.

    Args:
        mem: Memory reader (MockMemory, PymemMemoryAdapter, etc.)
        wrapper_instance: Address of the WrapperController singleton instance.
        wrapper_class: Address of the WrapperController class (for field lookup).

    Returns:
        RankScanResult with constructed and limited rank info.
    """
    result = RankScanResult()
    warnings: list[str] = []

    # Step 1: Find PlayerRankServiceWrapper field in WrapperController
    fields = get_class_fields(mem, wrapper_class)
    rank_wrapper_field = next(
        (f for f in fields if f.name == RANK_WRAPPER_FIELD), None
    )
    if rank_wrapper_field is None:
        warnings.append(f"Field '{RANK_WRAPPER_FIELD}' not found in WrapperController")
        result.warnings = warnings
        return result

    # Step 2: Read PlayerRankServiceWrapper pointer
    rank_wrapper_ptr = _read_ptr(mem, wrapper_instance + rank_wrapper_field.offset)
    if rank_wrapper_ptr == 0 or rank_wrapper_ptr < 0x100000:
        warnings.append("PlayerRankServiceWrapper pointer is null or invalid")
        result.warnings = warnings
        return result

    # Step 3: Find _combinedRankInfo field in PlayerRankServiceWrapper
    rank_wrapper_class = _read_ptr(mem, rank_wrapper_ptr)
    if rank_wrapper_class == 0 or rank_wrapper_class < 0x100000:
        warnings.append("PlayerRankServiceWrapper class pointer is invalid")
        result.warnings = warnings
        return result

    wrapper_fields = get_class_fields(mem, rank_wrapper_class)
    cri_field = next(
        (f for f in wrapper_fields if f.name == COMBINED_RANK_INFO_FIELD), None
    )
    if cri_field is None:
        warnings.append(f"Field '{COMBINED_RANK_INFO_FIELD}' not found in PlayerRankServiceWrapper")
        result.warnings = warnings
        return result

    # Step 4: Read _combinedRankInfo pointer
    cri_ptr = _read_ptr(mem, rank_wrapper_ptr + cri_field.offset)
    if cri_ptr == 0 or cri_ptr < 0x100000:
        warnings.append("_combinedRankInfo pointer is null or invalid")
        result.warnings = warnings
        return result

    result.raw_address = cri_ptr

    # Step 5: Get _combinedRankInfo class fields
    cri_class = _read_ptr(mem, cri_ptr)
    if cri_class == 0 or cri_class < 0x100000:
        warnings.append("_combinedRankInfo class pointer is invalid")
        result.warnings = warnings
        return result

    cri_fields = get_class_fields(mem, cri_class)
    field_map: dict[str, int] = {f.name: f.offset for f in cri_fields}

    # Step 6: Read playerId (string field)
    if PLAYER_ID_FIELD in field_map:
        player_id_ptr = _read_ptr(mem, cri_ptr + field_map[PLAYER_ID_FIELD])
        if player_id_ptr > 0x100000:
            result.player_id = read_il2cpp_string(mem, player_id_ptr)

    # Step 7: Read constructed and limited rank info
    for prefix in RANK_PREFIXES:
        rank_info = _read_rank_info(mem, cri_ptr, field_map, prefix)
        if prefix == "constructed":
            result.constructed = rank_info
        else:
            result.limited = rank_info

    result.warnings = warnings
    return result


def _read_rank_info(
    mem: MemoryReader,
    cri_ptr: int,
    field_map: dict[str, int],
    prefix: str,
) -> RankInfo:
    """Read a single rank info block (constructed or limited) from _combinedRankInfo.

    Reads fields like constructedClass, constructedLevel, etc.
    """
    def get_int(suffix: str) -> int:
        field_name = f"{prefix}{suffix}"
        if field_name in field_map:
            return _read_i32(mem, cri_ptr + field_map[field_name])
        return 0

    def get_u32(suffix: str) -> int:
        field_name = f"{prefix}{suffix}"
        if field_name in field_map:
            return _read_u32(mem, cri_ptr + field_map[field_name])
        return 0

    def get_string(suffix: str) -> str:
        field_name = f"{prefix}{suffix}"
        if field_name in field_map:
            str_ptr = _read_ptr(mem, cri_ptr + field_map[field_name])
            if str_ptr > 0x100000:
                return read_il2cpp_string(mem, str_ptr)
        return ""

    class_value = get_u32(CLASS_SUFFIX)
    return RankInfo(
        season_ordinal=get_int(SEASON_ORDINAL_SUFFIX),
        class_value=class_value,
        class_name=rank_class_name(class_value),
        level=get_int(LEVEL_SUFFIX),
        step=get_int(STEP_SUFFIX),
        wins=get_int(MATCHES_WON_SUFFIX),
        losses=get_int(MATCHES_LOST_SUFFIX),
        draws=get_int(MATCHES_DRAWN_SUFFIX),
        percentile=get_string(PERCENTILE_SUFFIX),
        leaderboard_place=get_int(LEADERBOARD_PLACE_SUFFIX),
    )


# ---------------------------------------------------------------------------
# Account scanning — navigate to AccountInformation and read fields
# ---------------------------------------------------------------------------

def scan_account_from_instance(
    mem: MemoryReader,
    wrapper_instance: int,
    wrapper_class: int,
) -> AccountInfo:
    """Read account info from a WrapperController instance.

    Navigates: WrapperController → AccountClient → AccountInformation
    Then reads DisplayName, AccountID, PersonaID, etc.

    Args:
        mem: Memory reader.
        wrapper_instance: Address of the WrapperController singleton instance.
        wrapper_class: Address of the WrapperController class.

    Returns:
        AccountInfo with account details.
    """
    result = AccountInfo()
    warnings: list[str] = []

    # Step 1: Find AccountClient field in WrapperController
    fields = get_class_fields(mem, wrapper_class)
    account_client_field = next(
        (f for f in fields if f.name == ACCOUNT_CLIENT_FIELD), None
    )
    if account_client_field is None:
        warnings.append(f"Field '{ACCOUNT_CLIENT_FIELD}' not found in WrapperController")
        result.warnings = warnings
        return result

    # Step 2: Read AccountClient pointer
    ac_ptr = _read_ptr(mem, wrapper_instance + account_client_field.offset)
    if ac_ptr == 0 or ac_ptr < 0x100000:
        warnings.append("AccountClient pointer is null or invalid")
        result.warnings = warnings
        return result

    # Step 3: Find AccountInformation field in AccountClient
    ac_class = _read_ptr(mem, ac_ptr)
    if ac_class == 0 or ac_class < 0x100000:
        warnings.append("AccountClient class pointer is invalid")
        result.warnings = warnings
        return result

    ac_fields = get_class_fields(mem, ac_class)
    ai_field = next(
        (f for f in ac_fields if f.name == ACCOUNT_INFO_FIELD), None
    )
    if ai_field is None:
        warnings.append(f"Field '{ACCOUNT_INFO_FIELD}' not found in AccountClient")
        result.warnings = warnings
        return result

    # Step 4: Read AccountInformation pointer
    ai_ptr = _read_ptr(mem, ac_ptr + ai_field.offset)
    if ai_ptr == 0 or ai_ptr < 0x100000:
        warnings.append("AccountInformation pointer is null or invalid")
        result.warnings = warnings
        return result

    # Step 5: Read all string fields from AccountInformation
    ai_class = _read_ptr(mem, ai_ptr)
    if ai_class == 0 or ai_class < 0x100000:
        warnings.append("AccountInformation class pointer is invalid")
        result.warnings = warnings
        return result

    ai_fields = get_class_fields(mem, ai_class)
    ai_field_map: dict[str, int] = {f.name: f.offset for f in ai_fields}

    for field_name in ACCOUNT_STRING_FIELDS:
        if field_name in ai_field_map:
            str_ptr = _read_ptr(mem, ai_ptr + ai_field_map[field_name])
            if str_ptr > 0x100000:
                value = read_il2cpp_string(mem, str_ptr)
                # Map to the appropriate attribute
                if field_name == "DisplayName":
                    result.display_name = value
                elif field_name == "AccountID":
                    result.account_id = value
                elif field_name == "PersonaID":
                    result.persona_id = value
                elif field_name == "GameID":
                    result.game_id = value
                elif field_name == "Email":
                    result.email = value
                elif field_name == "ExternalID":
                    result.external_id = value
                elif field_name == "CountryCode":
                    result.country_code = value
                elif field_name == "AccessToken":
                    result.access_token = value

    result.warnings = warnings
    return result


# ---------------------------------------------------------------------------
# Full scan entry points — discover WrapperController, then read ranks/account
# ---------------------------------------------------------------------------

@dataclass
class FullRankScanResult:
    """Combined result of ranks + account scan."""
    ranks: RankScanResult = field(default_factory=RankScanResult)
    account: AccountInfo = field(default_factory=AccountInfo)
    warnings: list[str] = field(default_factory=list)
    wrapper_instance: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranks": self.ranks.to_dict(),
            "account": self.account.to_dict(),
            "warnings": self.warnings,
        }


def scan_ranks_and_account(
    reader: Il2CppReader | MemoryReader,
    *,
    wrapper_instance: int = 0,
    wrapper_class: int = 0,
    type_info_table: int = 0,
    data_segment_base: int = 0,
    read_account: bool = True,
    debug: bool = False,
) -> FullRankScanResult:
    """Scan MTGA memory for player ranks and account info.

    This is the main entry point. It either uses pre-discovered addresses
    or discovers the WrapperController (PAPA) instance from the TypeInfoTable.

    Args:
        reader: Il2CppReader or MemoryReader instance.
        wrapper_instance: Pre-discovered WrapperController instance address (0 = discover).
        wrapper_class: Pre-discovered WrapperController class address (0 = discover).
        type_info_table: Pre-discovered TypeInfoTable address (0 = discover).
        data_segment_base: GameAssembly __DATA segment base (for discovery).
        read_account: Whether to also scan account info (default: True).
        debug: Enable backref discovery debug logging (default: False).

    Returns:
        FullRankScanResult with ranks and optional account info.
    """
    # Normalize reader
    if isinstance(reader, Il2CppReader):
        mem = reader.mem
    else:
        mem = reader

    warnings: list[str] = []

    # Fast path: locate a live WrapperController instance via metadata
    # backrefs, bypassing data_segment_base/TypeInfoTable discovery entirely
    # (same strategy as the deck scanner's discover_decks_manager_via_backref —
    # Il2CppClass structs live outside GameAssembly's segments on macOS). Only
    # attempted when the caller hasn't pre-supplied explicit addresses.
    if wrapper_instance == 0 and wrapper_class == 0:
        via_backref = discover_wrapper_controller_via_backref(mem, debug=debug)
        if via_backref is not None:
            wrapper_instance, wrapper_class = via_backref
            # Fall through to the shared scan below with discovered addresses.
        else:
            warnings.append("backref discovery for WrapperController failed; falling back to TypeInfoTable path")

    # Discover WrapperController class if not provided
    if wrapper_class == 0:
        if type_info_table == 0:
            if data_segment_base == 0:
                data_segment_base = discover_data_segment_base(mem)
            if data_segment_base == 0:
                warnings.append("no data_segment_base could be discovered for class discovery")
                return FullRankScanResult(warnings=warnings)
            from .il2cpp_nav import IL2CPP_OFFSETS
            type_info_table = _read_ptr(mem, data_segment_base + IL2CPP_OFFSETS["type_info_table_offset"])
            if type_info_table == 0:
                warnings.append("TypeInfoTable not found at data_segment_base")
                return FullRankScanResult(warnings=warnings)

        # Look for WrapperController class (not PAPA — WrapperController is the
        # actual class name in Assembly-CSharp)
        found_class = find_class_by_name(mem, type_info_table, "WrapperController")
        if found_class is None:
            # Fallback: try PAPA (older naming)
            found_class = find_class_by_name(mem, type_info_table, "PAPA")
        if found_class is None:
            warnings.append("WrapperController class not found in TypeInfoTable")
            return FullRankScanResult(warnings=warnings)
        wrapper_class = found_class

    # Discover WrapperController instance if not provided
    if wrapper_instance == 0:
        found = find_papa_instance(mem, wrapper_class)
        if found is None:
            warnings.append("WrapperController instance not found in heap scan")
            return FullRankScanResult(warnings=warnings)
        wrapper_instance = found

    # Scan ranks
    rank_result = scan_ranks_from_instance(mem, wrapper_instance, wrapper_class)
    warnings.extend(rank_result.warnings)

    # Scan account if requested
    account_result = AccountInfo()
    if read_account:
        account_result = scan_account_from_instance(mem, wrapper_instance, wrapper_class)
        warnings.extend(account_result.warnings)

    return FullRankScanResult(
        ranks=rank_result,
        account=account_result,
        warnings=warnings,
        wrapper_instance=wrapper_instance,
    )


# ---------------------------------------------------------------------------
# Convenience: scan only ranks
# ---------------------------------------------------------------------------

def scan_ranks(
    reader: Il2CppReader | MemoryReader,
    *,
    wrapper_instance: int = 0,
    wrapper_class: int = 0,
    type_info_table: int = 0,
    data_segment_base: int = 0,
) -> RankScanResult:
    """Scan MTGA memory for player ranks only (no account info).

    Convenience wrapper around scan_ranks_and_account with read_account=False.

    Returns:
        RankScanResult with constructed and limited rank info.
    """
    full = scan_ranks_and_account(
        reader,
        wrapper_instance=wrapper_instance,
        wrapper_class=wrapper_class,
        type_info_table=type_info_table,
        data_segment_base=data_segment_base,
        read_account=False,
    )
    return full.ranks


# ---------------------------------------------------------------------------
# Formatting helpers for CLI and dashboard
# ---------------------------------------------------------------------------

def format_rank_summary(ranks: RankScanResult) -> str:
    """Format rank data as a human-readable summary string.

    Example: "🧢 Platinum #1274 (4-2 diese Saison)"
    """
    c = ranks.constructed
    if c.class_value == 0:
        return "Kein Rang (unranked)"

    parts: list[str] = [c.class_name]
    if c.level > 0:
        parts.append(f"Level {c.level}")
    if c.step > 0:
        parts.append(f"Step {c.step}")

    record = ""
    if c.wins or c.losses or c.draws:
        record = f" ({c.wins}-{c.losses}"
        if c.draws:
            record += f"-{c.draws}"
        record += " diese Saison)"

    leaderboard = ""
    if c.leaderboard_place > 0:
        leaderboard = f" #{c.leaderboard_place}"

    return f"{' '.join(parts)}{leaderboard}{record}"


def format_rank_table(ranks: RankScanResult) -> str:
    """Format rank data as a multi-line table for CLI output."""
    lines: list[str] = []
    if ranks.player_id:
        lines.append(f"Player ID: {ranks.player_id}")
    lines.append("")

    for label, info in (("Constructed", ranks.constructed), ("Limited", ranks.limited)):
        lines.append(f"  {label}:")
        lines.append(f"    Class:       {info.class_name} ({info.class_value})")
        lines.append(f"    Level:       {info.level}")
        lines.append(f"    Step:        {info.step}")
        lines.append(f"    Season:      {info.season_ordinal}")
        lines.append(f"    Record:      {info.wins}-{info.losses}-{info.draws}")
        if info.percentile:
            lines.append(f"    Percentile:  {info.percentile}")
        if info.leaderboard_place > 0:
            lines.append(f"    Leaderboard: #{info.leaderboard_place}")
        lines.append("")

    return "\n".join(lines)


def format_account_summary(account: AccountInfo) -> str:
    """Format account info as a human-readable summary."""
    parts: list[str] = []
    if account.display_name:
        parts.append(account.display_name)
    if account.country_code:
        parts.append(f"[{account.country_code}]")
    if account.account_id:
        parts.append(f"ID: {account.account_id[:8]}...")
    return " ".join(parts) if parts else "Keine Account-Info"


def format_account_table(account: AccountInfo) -> str:
    """Format account info as a multi-line table for CLI output."""
    lines: list[str] = ["Account Information:"]

    if account.display_name:
        lines.append(f"  Display Name: {account.display_name}")
    if account.account_id:
        lines.append(f"  Account ID:  {account.account_id}")
    if account.persona_id:
        lines.append(f"  Persona ID:  {account.persona_id}")
    if account.game_id:
        lines.append(f"  Game ID:     {account.game_id}")
    if account.country_code:
        lines.append(f"  Country:     {account.country_code}")
    if account.email:
        lines.append(f"  Email:       {account.email}")
    if account.external_id:
        lines.append(f"  External ID: {account.external_id}")

    if len(lines) == 1:
        lines.append("  (keine Daten gefunden)")

    return "\n".join(lines)
