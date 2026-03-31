from enum import StrEnum


class TournamentType(StrEnum):
    WEEKLY = "weekly"
    SPECIAL = "special"
    MONTHLY = "monthly"


class TournamentGameKey(StrEnum):
    AUTOCHESS = "autochess"
    PUBG = "pubg"


class TournamentFormatKey(StrEnum):
    SOLO_32 = "solo_32"
    SOLO_BATTLE = "solo_battle"


class TournamentStatus(StrEnum):
    DRAFT = "draft"
    REGISTRATION_OPEN = "registration_open"
    REGISTRATION_LOCKED = "registration_locked"
    ZONES_CREATED = "zones_created"
    ZONES_RUNNING = "zones_running"
    SEMIS_CREATED = "semis_created"
    SEMIS_RUNNING = "semis_running"
    FINAL_CREATED = "final_created"
    FINAL_RUNNING = "final_running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EntryStatus(StrEnum):
    REGISTERED = "registered"
    CONFIRMED = "confirmed"
    WAITLIST = "waitlist"
    ACTIVE = "active"
    REPLACED_OUT = "replaced_out"
    REPLACEMENT_IN = "replacement_in"
    ELIMINATED = "eliminated"
    QUALIFIED = "qualified"
    CHAMPION = "champion"


class PaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class StageType(StrEnum):
    ZONE = "zone"
    SEMI = "semi"
    FINAL = "final"


class StageStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    FINISHED = "finished"


class GameStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
