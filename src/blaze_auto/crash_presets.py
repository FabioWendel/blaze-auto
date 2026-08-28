"""Atalhos de configuração; não são previsões ou recomendações de lucro."""
from dataclasses import dataclass

from .strategy import DEFAULT_PATTERN


@dataclass(frozen=True)
class CrashPreset:
    pattern: str
    cashout: str
    experimental: bool = False


PRESETS = {
    "original": CrashPreset(DEFAULT_PATTERN, "5.00"),
    "baixas-media": CrashPreset("BBBBM", "1.50", experimental=True),
}
EXPERIMENTAL_WARNING = (
    "EXPERIMENTAL | BBBBM/1.50x perdeu no teste histórico; mais sinais não significam lucro. "
    "Teste em simulação. Baixas anteriores não garantem recuperação."
)


def resolve_preset(name: str, pattern: str | None, cashout: str | None) -> tuple[str, str]:
    """Explicit options always override the preset, including the original values."""
    preset = PRESETS[name]
    return (preset.pattern if pattern is None else pattern.strip().upper(),
            preset.cashout if cashout is None else cashout)
