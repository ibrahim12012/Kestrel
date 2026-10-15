# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/safety.py
============
Risk-tiered permission framework for AI-initiated system actions.

Two things live here:

  1. An explicit allow-list for tool_run_command (ai/actions.py), replacing
     the old "hand Gemini's command string straight to a shell" approach.
     Every base command Kestrel is willing to run is named here, tagged with
     a risk tier (LOW / MEDIUM / HIGH); anything not listed is refused
     outright, and a short list of genuinely destructive commands is
     blocked even though nothing let them through the allow-list in the
     first place (defense in depth, and a clearer refusal message).

  2. A SafetyLimiter that caps how many actions of each risk tier can run
     unattended within one run - the higher the tier, the fewer of them
     run before Kestrel has to stop and let a human look again, regardless
     of how many steps are left in the automation budget.
"""

from enum import Enum


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Base cmd.exe commands tool_run_command is allowed to run, each tagged with
# its risk tier. Matched against the FIRST token of the command only (after
# stripping a trailing .exe/.bat/.cmd) - arguments themselves aren't
# validated beyond the metacharacter check in ai/actions.py, since that's
# what keeps this a fixed, auditable list instead of a growing pile of
# per-argument special cases.
ALLOWED_COMMANDS: dict[str, RiskLevel] = {
    # ── read-only / informational ──────────────────────────────────────
    "dir":        RiskLevel.LOW,
    "type":       RiskLevel.LOW,
    "echo":       RiskLevel.LOW,
    "cd":         RiskLevel.LOW,
    "where":      RiskLevel.LOW,
    "whoami":     RiskLevel.LOW,
    "tasklist":   RiskLevel.LOW,
    "ipconfig":   RiskLevel.LOW,
    "systeminfo": RiskLevel.LOW,
    "ver":        RiskLevel.LOW,
    "hostname":   RiskLevel.LOW,
    "tree":       RiskLevel.LOW,
    "findstr":    RiskLevel.LOW,
    "ping":       RiskLevel.LOW,

    # ── dev tooling / package managers - can install or run arbitrary
    #    code, but that's the whole point of asking for them ───────────
    "python":     RiskLevel.MEDIUM,
    "python3":    RiskLevel.MEDIUM,
    "py":         RiskLevel.MEDIUM,
    "node":       RiskLevel.MEDIUM,
    "npm":        RiskLevel.MEDIUM,
    "npx":        RiskLevel.MEDIUM,
    "pip":        RiskLevel.MEDIUM,
    "pip3":       RiskLevel.MEDIUM,
    "git":        RiskLevel.MEDIUM,
    "winget":     RiskLevel.MEDIUM,
    "code":       RiskLevel.MEDIUM,

    # ── modifies the filesystem, but scoped to files/folders (not the
    #    whole system) ───────────────────────────────────────────────
    "copy":       RiskLevel.MEDIUM,
    "xcopy":      RiskLevel.MEDIUM,
    "move":       RiskLevel.MEDIUM,
    "md":         RiskLevel.MEDIUM,
    "mkdir":      RiskLevel.MEDIUM,

    # ── destructive / can affect other running software ────────────────
    "del":        RiskLevel.HIGH,
    "erase":      RiskLevel.HIGH,
    "rd":         RiskLevel.HIGH,
    "rmdir":      RiskLevel.HIGH,
    "taskkill":   RiskLevel.HIGH,
    "reg":        RiskLevel.HIGH,
    "shutdown":   RiskLevel.HIGH,
    "netsh":      RiskLevel.HIGH,
    "sfc":        RiskLevel.HIGH,
}

# Never allowed, no matter the tier - large blast radius (reformatting/
# repartitioning a drive, editing the boot config, an unrestricted script
# shell). A human should be doing this at a real terminal, not through
# Kestrel. Checked even though none of these are in ALLOWED_COMMANDS, so the
# refusal message can say *why* rather than just "not in the list".
BLOCKED_COMMANDS: frozenset = frozenset({
    "format", "diskpart", "bcdedit", "fsutil", "vssadmin", "powershell",
    "wmic", "cipher",
})

# Characters that would let one command string chain into several (&, &&,
# |, ||), redirect output over an arbitrary file (>, >>), or expand an
# environment variable unpredictably (%). tool_run_command refuses any
# command containing one of these outright - one plain command with plain
# arguments per call, nothing that reaches outside that shape.
DISALLOWED_METACHARACTERS = ("&", "|", ">", "<", "^", "%")

# Words/phrases (checked as substrings, case-insensitive, underscores
# normalized to spaces) that mark a UI action as destructive - spec
# section 4's "Safety Interception Hooks": any action whose accessible
# name/id matches one of these gets a mandatory confirmation boundary
# before ai/discovery/compiler.py's compiled function will actually run
# it, and ai/discovery/mapper.py never triggers one of these itself
# while just mapping an app (see mapper.py's EXPLORING branch) - a
# mapping run should never be the thing that deletes, purges, or
# submits an order.
DESTRUCTIVE_ACTION_KEYWORDS: frozenset = frozenset({
    "delete", "remove", "purge", "format", "erase", "wipe", "uninstall",
    "discard", "reset", "clear all", "empty trash", "submit order",
    "place order", "confirm purchase", "buy now", "checkout", "pay now",
    "cancel subscription", "deactivate", "close account", "sign out",
    "log out everywhere", "overwrite", "unsubscribe", "revoke",
    # Window-chrome actions - these don't destroy user DATA the way the
    # keywords above do, but activating one mid-mapping-run is still
    # exactly the kind of thing this list exists to prevent: it derails
    # the mapper itself (minimizing/closing the target window means every
    # accessibility query right after it fails or returns something
    # unrelated). Bare "close"/"fermer" deliberately excluded - those are
    # also legitimate BACK-CONTROL names (see accessibility.py's
    # _BACK_CONTROL_NAMES) that find_back_control() intentionally DOES
    # click to navigate back during backtracking; adding them here would
    # fight that mechanism instead of protecting the run.
    "minimize", "minimise", "reduce", "réduire", "minimiser",
    "quit", "exit", "quitter", "shut down", "shutdown", "arrêter",
    "restart", "redémarrer", "sign out everywhere",
    "maximize", "maximise", "agrandir", "restore down", "restaurer",
})

# The same, for actions that reach other people or cost money (sending,
# posting, paying, calling, accepting an invitation) and for the "lose
# my changes" answers, in the languages an app's buttons are most often
# in. Matched as whole words, so "post" doesn't catch "Postal code" and
# "send" doesn't catch "Sender". Words in scripts written without spaces
# (Chinese, Japanese) or with combining vowel signs (Hindi) are matched
# anywhere in the label instead. "Forward", "share" and "transfer" are
# left out: in most apps they open something rather than do something.
# So are Spanish "aceptar" and "seguir": they are the OK and Continue of
# every Spanish dialog and wizard.
DESTRUCTIVE_ACTION_WORDS: frozenset = frozenset({
    # English
    "send", "send now", "post", "publish", "reply", "reply all", "pay",
    "purchase", "buy", "submit", "order now", "don't save", "do not save",
    "dont save", "log out", "logout", "log off", "sign off", "empty",
    "delete account", "clear data", "clear history", "call", "subscribe",
    "accept", "decline", "reject", "approve", "install", "update now",
    "upgrade", "like", "follow", "repost", "retweet", "quick print",
    # French
    "supprimer", "effacer", "envoyer", "publier", "payer", "acheter",
    "commander", "répondre", "répondre à tous", "ne pas enregistrer",
    "désinstaller", "vider", "se déconnecter", "déconnexion", "réinitialiser",
    "formater", "soumettre", "appeler", "s'abonner", "accepter", "refuser",
    "installer", "j'aime", "suivre",
    # Spanish
    "eliminar", "borrar", "enviar", "publicar", "pagar", "comprar",
    "responder", "no guardar", "desinstalar", "vaciar", "cerrar sesión",
    "restablecer", "formatear", "llamar", "suscribirse", "rechazar",
    "instalar", "me gusta",
    # Portuguese
    "excluir", "apagar", "não salvar", "não guardar", "esvaziar",
    "sair da conta", "terminar sessão", "redefinir", "formatar", "assinar",
    "aceitar", "recusar", "curtir",
    # German
    "löschen", "entfernen", "senden", "absenden", "veröffentlichen",
    "bezahlen", "kaufen", "antworten", "nicht speichern", "deinstallieren",
    "leeren", "abmelden", "zurücksetzen", "formatieren", "anrufen",
    "abonnieren", "annehmen", "ablehnen", "installieren", "gefällt mir",
    "folgen",
    # Italian
    "elimina", "cancella", "invia", "pubblica", "paga", "acquista", "compra",
    "rispondi", "non salvare", "disinstalla", "svuota", "esci", "disconnetti",
    "reimposta", "formatta", "chiama", "iscriviti", "accetta", "rifiuta",
    "installa", "mi piace", "segui",
    # Arabic
    "حذف", "إزالة", "إرسال", "نشر", "دفع", "شراء", "رد", "عدم الحفظ",
    "لا تحفظ", "إلغاء التثبيت", "تفريغ", "تسجيل الخروج", "إعادة تعيين",
    "تهيئة", "اشتراك", "قبول", "رفض", "تثبيت",
    # Hindi
    "हटाएं", "हटाएँ", "मिटाएं", "भेजें", "प्रकाशित करें", "भुगतान", "खरीदें",
    "जवाब दें", "सहेजें नहीं", "अनइंस्टॉल", "साइन आउट", "लॉग आउट", "रीसेट",
    "फ़ॉर्मेट", "कॉल करें", "सदस्यता लें", "स्वीकार करें", "अस्वीकार करें",
    "इंस्टॉल करें",
    # Chinese (simplified and traditional)
    "删除", "刪除", "移除", "清空", "发送", "發送", "发布", "發佈", "發布",
    "付款", "支付", "购买", "購買", "回复", "回覆", "不保存", "不儲存",
    "卸载", "解除安裝", "退出登录", "登出", "注销", "註銷", "重置", "格式化",
    "提交", "下单", "下單", "呼叫", "拨打", "订阅", "訂閱", "接受", "拒绝",
    "拒絕", "安装", "安裝", "点赞", "按讚", "关注", "關注",
    # Japanese
    "削除", "送信", "投稿", "支払", "購入", "返信", "保存しない",
    "アンインストール", "サインアウト", "ログアウト", "リセット", "フォーマット",
    "通話", "承諾", "拒否", "インストール", "いいね", "フォロー",
    # Korean
    "삭제", "보내기", "전송", "게시", "결제", "구매", "답장", "저장 안 함",
    "로그아웃", "초기화", "포맷", "통화", "구독", "수락", "거절", "설치",
    "좋아요", "팔로우",
})


def _written_without_word_breaks(word: str) -> bool:
    """Devanagari and the other Indic scripts (their vowel signs aren't
    word characters to a regex), Thai, Chinese, Japanese and Korean."""
    for ch in word:
        code = ord(ch)
        if (0x0900 <= code <= 0x0E7F or 0x1100 <= code <= 0x11FF
                or 0x3040 <= code <= 0x30FF or 0x3130 <= code <= 0x318F
                or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
                or 0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF):
            return True
    return False


def _compile_destructive_words():
    import re

    spaced = sorted((w for w in DESTRUCTIVE_ACTION_WORDS if not _written_without_word_breaks(w)),
                    key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in spaced) + r")(?!\w)")
    anywhere = tuple(w for w in DESTRUCTIVE_ACTION_WORDS if _written_without_word_breaks(w))
    return pattern, anywhere


_DESTRUCTIVE_WORDS_RE, _DESTRUCTIVE_ANYWHERE = _compile_destructive_words()


def is_destructive_label(label: "str | None") -> bool:
    """
    True if `label` (an accessible name, an action id, a macro/button
    description - anything human-readable) matches a destructive-action
    keyword. Used both at discovery time (mapper.py) and at compile
    time (compiler.py) so the two never disagree about what counts as
    destructive - one list, checked the same way in both places.
    """
    if not label:
        return False
    normalized = " ".join(label.strip().lower().replace("_", " ").replace("’", "'").split())
    if any(keyword in normalized for keyword in DESTRUCTIVE_ACTION_KEYWORDS):
        return True
    if _DESTRUCTIVE_WORDS_RE.search(normalized):
        return True
    return any(word in normalized for word in _DESTRUCTIVE_ANYWHERE)


# Typical size of a Windows title-bar minimize/maximize/close button
# used by is_likely_window_chrome_button below. Generous on purpose (a
# few px either way shouldn't matter) since this is a heuristic, not an
# exact match against a known constant.
_TITLEBAR_BUTTON_MAX_WIDTH = 60
_TITLEBAR_BUTTON_MAX_HEIGHT = 45
_TITLEBAR_ZONE_HEIGHT = 50  # how far down from the window's top edge counts as "the title bar area"
_TITLEBAR_ZONE_WIDTH = 160  # how far in from the window's right edge covers all three buttons


def is_likely_window_chrome_button(element_bbox: dict, root_bbox: dict) -> bool:
    """
    True if `element_bbox` sits where a window's own minimize/maximize/
    close buttons live - small, hugging the top-right corner of the
    window's own bounding box (`root_bbox`) - regardless of what it's
    labeled.

    Why this exists alongside is_destructive_label rather than just
    adding more keywords to that list: "close"/"fermer" is deliberately
    NOT a destructive keyword (see DESTRUCTIVE_ACTION_KEYWORDS' own
    comment) because it's also the legitimate name of in-app back/dismiss
    buttons that find_back_control() intentionally DOES click to
    navigate backward. But the window's OWN title-bar close button is a
    completely different, far more dangerous thing than an in-app close
    button - clicking IT ends the mapping run's whole target window, not
    just closes a dialog inside it - and it can't be told apart from an
    in-app "Close" button by label alone. Position can, though: an in-app
    close/back button is essentially never positioned exactly where the
    OS itself always puts the real title-bar controls. This deliberately
    only fires for elements matching THIS specific position+size profile,
    erring toward false negatives (missing a real chrome button
    positioned unusually) over false positives (blocking a legitimate
    in-app button that merely happens to sit in that corner) - a missed
    chrome button still can't be triggered if its LABEL also happens to
    match is_destructive_label (minimize/maximize already are, above);
    this only adds coverage for the one case neither label-matching
    approach can otherwise catch: an unlabeled or oddly-labeled close
    button in that exact corner.
    """
    try:
        ex, ey, ew, eh = element_bbox["x"], element_bbox["y"], element_bbox["w"], element_bbox["h"]
        rx, ry, rw, rh = root_bbox["x"], root_bbox["y"], root_bbox["w"], root_bbox["h"]
    except (KeyError, TypeError):
        return False

    if ew <= 0 or eh <= 0 or rw <= 0 or rh <= 0:
        return False
    if ew > _TITLEBAR_BUTTON_MAX_WIDTH or eh > _TITLEBAR_BUTTON_MAX_HEIGHT:
        return False

    # A real title-bar button only ever takes up a small FRACTION of the
    # window it belongs to - never anywhere close to half its width or
    # height. Without this check, the fixed pixel zone below (sized for
    # a normal desktop window) can end up bigger than a genuinely small
    # window/element, which makes literally every element in that window
    # "near the top-right corner" by definition - regardless of where it
    # actually sits - and everything in it gets misclassified as chrome
    # and skipped. (This is exactly what made every button on a small
    # test fixture window register as a title-bar close button.)
    if ew >= rw * 0.5 or eh >= rh * 0.5:
        return False

    # Same reasoning applied to the corner zone itself: cap it at a
    # fraction of the window's own size so a small window can't have its
    # entire surface count as "the corner" just because the fixed pixel
    # constants (tuned for a normal-sized window) happen to exceed the
    # window's own dimensions.
    zone_height = min(_TITLEBAR_ZONE_HEIGHT, rh * 0.25)
    zone_width = min(_TITLEBAR_ZONE_WIDTH, rw * 0.25)

    near_top = (ey - ry) <= zone_height
    near_right_edge = ((rx + rw) - (ex + ew)) <= zone_width
    return near_top and near_right_edge


# How many actions of each tier are allowed to run, unattended, within one
# "session" (see SafetyLimiter.reset() call sites: a fresh autonomous run,
# or an explicit reset from Settings). Overridable per-tier from Settings
# see ai.main.get_safety_limit/set_safety_limit.
DEFAULT_MAX_ACTIONS: dict[RiskLevel, int] = {
    RiskLevel.LOW: 100,
    RiskLevel.MEDIUM: 30,
    RiskLevel.HIGH: 5,
}


def _base_name(command: str) -> str:
    name = command.strip().lower()
    for suffix in (".exe", ".bat", ".cmd"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def classify_command(base_command: str) -> "RiskLevel | None":
    """Risk tier for a base command, or None if it isn't on the allow-list."""
    return ALLOWED_COMMANDS.get(_base_name(base_command))


def is_blocked_command(base_command: str) -> bool:
    return _base_name(base_command) in BLOCKED_COMMANDS


class SafetyLimiter:
    """
    Tracks how many actions of each risk tier have run since the last
    reset(), and refuses further ones past the configured cap.
    """

    def __init__(self, limits: "dict[RiskLevel, int] | None" = None):
        self._limits = dict(limits or DEFAULT_MAX_ACTIONS)
        self._counts = {level: 0 for level in RiskLevel}

    def set_limit(self, level: RiskLevel, value: int) -> None:
        self._limits[level] = max(0, int(value))

    def get_limit(self, level: RiskLevel) -> int:
        return self._limits.get(level, 0)

    def reset(self) -> None:
        """Clears this run's counts (not the configured limits)."""
        self._counts = {level: 0 for level in RiskLevel}

    def remaining(self, level: RiskLevel) -> int:
        return max(0, self._limits.get(level, 0) - self._counts[level])

    def check_and_record(self, level: RiskLevel) -> "str | None":
        """
        Call right before actually performing an action of the given risk
        level. Returns None (and records the action) if it's OK to
        proceed, or a refusal string if this tier's cap has already been
        reached this session.
        """
        limit = self._limits.get(level, 0)
        if self._counts[level] >= limit:
            return (
                f"SAFETY LIMIT REACHED: {limit} {level.value}-risk action(s) "
                f"already ran this session. No more {level.value}-risk "
                f"actions will run until a new task/session starts, or the "
                f"limit is raised in Settings."
            )
        self._counts[level] += 1
        return None


# One shared limiter for the whole process - chat tools, macro playback,
# and autonomous mode all draw from the same per-session budget, since any
# of them can run back-to-back within the same running app.
limiter = SafetyLimiter()
