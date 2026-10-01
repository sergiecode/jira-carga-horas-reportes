"""Cálculo de períodos de fechas para los reportes."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Periodo:
    """Rango de fechas con ambos extremos incluidos."""

    desde: date
    hasta: date

    def dias(self) -> list[date]:
        cantidad = (self.hasta - self.desde).days + 1
        return [self.desde + timedelta(days=i) for i in range(cantidad)]

    def contiene(self, fecha: date) -> bool:
        return self.desde <= fecha <= self.hasta

    def inicio_epoch_ms(self, zona: ZoneInfo) -> int:
        """Primer instante del período (00:00 de `desde` en la zona dada), en epoch ms."""
        inicio = datetime.combine(self.desde, time.min, tzinfo=zona)
        return int(inicio.timestamp() * 1000)


def hoy_en(zona: ZoneInfo) -> date:
    return datetime.now(zona).date()


def ultimos_dias(dias: int, zona: ZoneInfo, hoy: date | None = None) -> Periodo:
    """Los últimos `dias` días con hoy incluido (de hoy-(dias-1) a hoy)."""
    hoy = hoy or hoy_en(zona)
    return Periodo(desde=hoy - timedelta(days=dias - 1), hasta=hoy)
