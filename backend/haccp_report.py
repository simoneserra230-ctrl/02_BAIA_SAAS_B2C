"""
BAD.S — Servizio Generazione Registro HACCP Mensile
Conforme: Reg. CE 852/2004 | Gamco/ANFOS 2025 | SpazioHoReCa 2025
"""

import io
from datetime import datetime, date
from typing import Optional
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.platypus import KeepTogether
from reportlab.lib.colors import HexColor

# ── Palette BAD.S ────────────────────────────────────────────────────────────
C_PRIMARY   = HexColor("#1A1A2E")   # intestazioni principali
C_ACCENT    = HexColor("#2E86AB")   # bande blu
C_SUCCESS   = HexColor("#27AE60")   # OK
C_WARNING   = HexColor("#F39C12")   # warning
C_DANGER    = HexColor("#E74C3C")   # critical / alert
C_LIGHT     = HexColor("#F0F4F8")   # sfondo celle header
C_BORDER    = HexColor("#CBD5E0")   # bordi tabella
C_WHITE     = colors.white
C_MUTED     = HexColor("#718096")   # testo secondario

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


@dataclass
class TemperatureReading:
    data: str
    ora: str
    zona: str
    sensor_id: str
    temperatura: float
    temp_min: float
    temp_max: float
    alert: bool
    severity: str   # ok | warning | critical
    rilevato_da: str = "iot"
    operatore: str = ""
    azione_correttiva: str = ""


@dataclass
class HACCPReportData:
    hotel_name: str
    hotel_citta: str
    hotel_piva: str
    responsabile_haccp: str
    anno: int
    mese: int
    lettori: list[TemperatureReading] = field(default_factory=list)
    note_generali: str = ""
    data_compilazione: str = ""
    firma_responsabile: str = ""


def _severity_color(sev: str) -> HexColor:
    return {
        "ok":       C_SUCCESS,
        "warning":  C_WARNING,
        "critical": C_DANGER,
    }.get(sev, C_MUTED)


def _severity_label(sev: str) -> str:
    return {
        "ok":       "CONFORME",
        "warning":  "AVVISO",
        "critical": "CRITICO",
    }.get(sev, sev.upper())


ZONE_RANGES = {
    "cella_frigo":     (0.0,   4.0,  "Refrigerazione"),
    "cella_surgelati": (-22.0, -18.0, "Surgelazione"),
    "zona_calda":      (65.0,  100.0, "Servizio caldo"),
    "cantina":         (10.0,  18.0,  "Cantina vini"),
    "frigo_bar":       (2.0,   6.0,   "Frigorifero bar"),
}

MESI_IT = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"
]


class HACCPPdfGenerator:

    def __init__(self, data: HACCPReportData):
        self.data = data
        self.buf = io.BytesIO()
        self.styles = getSampleStyleSheet()
        self._setup_styles()

    def _setup_styles(self):
        s = self.styles
        base = dict(fontName="Helvetica", leading=14)

        self.sTitle = ParagraphStyle("sTitle",
            fontName="Helvetica-Bold", fontSize=16,
            textColor=C_PRIMARY, alignment=TA_CENTER, spaceAfter=4)

        self.sSub = ParagraphStyle("sSub",
            fontName="Helvetica", fontSize=9,
            textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=2)

        self.sSection = ParagraphStyle("sSection",
            fontName="Helvetica-Bold", fontSize=10,
            textColor=C_WHITE, alignment=TA_LEFT,
            spaceAfter=6, spaceBefore=14,
            backColor=C_ACCENT,
            leftIndent=-MARGIN + 18*mm,
            rightIndent=-MARGIN + 18*mm,
            borderPad=4)

        self.sBody = ParagraphStyle("sBody",
            fontName="Helvetica", fontSize=8,
            textColor=C_PRIMARY, leading=12)

        self.sBodySm = ParagraphStyle("sBodySm",
            fontName="Helvetica", fontSize=7,
            textColor=C_MUTED, leading=10)

        self.sFooter = ParagraphStyle("sFooter",
            fontName="Helvetica", fontSize=7,
            textColor=C_MUTED, alignment=TA_CENTER)

        self.sBold = ParagraphStyle("sBold",
            fontName="Helvetica-Bold", fontSize=8,
            textColor=C_PRIMARY, leading=12)

    # ── Header & Footer ──────────────────────────────────────────────────────

    def _on_page(self, canvas, doc):
        canvas.saveState()
        w, h = A4

        # Banda superiore
        canvas.setFillColor(C_PRIMARY)
        canvas.rect(0, h - 14*mm, w, 14*mm, fill=1, stroke=0)
        canvas.setFillColor(C_WHITE)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(MARGIN, h - 9*mm, "BAD.S UNIFIED PLATFORM")
        canvas.setFont("Helvetica", 8)
        label = f"REGISTRO HACCP — {self.data.hotel_name} — {MESI_IT[self.data.mese].upper()} {self.data.anno}"
        canvas.drawRightString(w - MARGIN, h - 9*mm, label)

        # Banda inferiore
        canvas.setFillColor(C_LIGHT)
        canvas.rect(0, 0, w, 10*mm, fill=1, stroke=0)
        canvas.setFillColor(C_MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(MARGIN, 3.5*mm,
            "Reg. CE 852/2004 | Codex Alimentarius 7 Principi HACCP | Disposizioni 2025")
        canvas.drawRightString(w - MARGIN, 3.5*mm,
            f"Pag. {doc.page}")

        canvas.restoreState()

    # ── Cover ────────────────────────────────────────────────────────────────

    def _build_cover(self) -> list:
        d = self.data
        mese_label = f"{MESI_IT[d.mese]} {d.anno}"
        story = []

        story.append(Spacer(1, 22*mm))

        # Logo testuale
        story.append(Paragraph("BAD.S UNIFIED PLATFORM", ParagraphStyle(
            "logo", fontName="Helvetica-Bold", fontSize=22,
            textColor=C_ACCENT, alignment=TA_CENTER, spaceAfter=2)))

        story.append(Paragraph("Hospitality Intelligence & F&B Management", self.sSub))
        story.append(Spacer(1, 6*mm))

        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 6*mm))

        story.append(Paragraph(
            "REGISTRO HACCP MENSILE DIGITALE",
            ParagraphStyle("doc_title", fontName="Helvetica-Bold", fontSize=18,
                           textColor=C_PRIMARY, alignment=TA_CENTER, spaceAfter=4)))

        story.append(Paragraph(
            f"Piano di Autocontrollo — {mese_label}",
            ParagraphStyle("doc_sub", fontName="Helvetica", fontSize=13,
                           textColor=C_ACCENT, alignment=TA_CENTER, spaceAfter=20)))

        story.append(Spacer(1, 6*mm))

        # Scheda struttura
        info_data = [
            ["STRUTTURA RICETTIVA", d.hotel_name],
            ["CITTÀ", d.hotel_citta],
            ["PARTITA IVA", d.hotel_piva],
            ["RESPONSABILE HACCP", d.responsabile_haccp],
            ["PERIODO DI RIFERIMENTO", mese_label],
            ["DATA COMPILAZIONE", d.data_compilazione or datetime.now().strftime("%d/%m/%Y")],
        ]
        info_table = Table(info_data, colWidths=[55*mm, 100*mm])
        info_table.setStyle(TableStyle([
            ("FONTNAME",    (0, 0), (-1, -1), "Helvetica"),
            ("FONTNAME",    (0, 0), (0, -1),  "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 9),
            ("TEXTCOLOR",   (0, 0), (0, -1),  C_MUTED),
            ("TEXTCOLOR",   (1, 0), (1, -1),  C_PRIMARY),
            ("BACKGROUND",  (0, 0), (-1, -1), C_WHITE),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_WHITE, C_LIGHT]),
            ("GRID",        (0, 0), (-1, -1), 0.3, C_BORDER),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 8*mm))

        # KPI rapidi
        total  = len(d.lettori)
        alerts = sum(1 for r in d.lettori if r.alert)
        crits  = sum(1 for r in d.lettori if r.severity == "critical")
        confs  = total - alerts

        kpi_data = [
            ["Rilevazioni totali", "Conformi", "Avvisi", "Critici"],
            [str(total), str(confs), str(alerts - crits), str(crits)],
        ]
        kpi_colors = [C_ACCENT, C_SUCCESS, C_WARNING, C_DANGER]
        kpi_table = Table(kpi_data, colWidths=[38*mm]*4)
        ts = TableStyle([
            ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTNAME",    (0, 1), (-1, 1),  "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, 0),  7),
            ("FONTSIZE",    (0, 1), (-1, 1),  18),
            ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",  (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING",(0,0), (-1, -1), 7),
            ("GRID",        (0, 0), (-1, -1), 0.3, C_BORDER),
        ])
        for i, col in enumerate(kpi_colors):
            ts.add("TEXTCOLOR", (i, 0), (i, 0), C_MUTED)
            ts.add("TEXTCOLOR", (i, 1), (i, 1), col)
        kpi_table.setStyle(ts)
        story.append(kpi_table)

        story.append(Spacer(1, 6*mm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 4*mm))

        # Riferimenti normativi
        story.append(Paragraph(
            "Riferimenti normativi: Reg. CE 852/2004 — Igiene prodotti alimentari | "
            "Codex Alimentarius — 7 Principi HACCP | ISO 22000:2018 | "
            "Disposizioni HACCP 2025 (registri digitali, monitoraggio IoT, formazione periodica)",
            self.sBodySm))

        story.append(PageBreak())
        return story

    # ── Sezione: CCP Checklist ───────────────────────────────────────────────

    def _build_ccp_checklist(self) -> list:
        story = []
        story.append(Paragraph("  PUNTI CRITICI DI CONTROLLO (CCP) — CHECKLIST MENSILE", self.sSection))
        story.append(Spacer(1, 3*mm))

        ccp_items = [
            ("CCP 1", "Ricevimento MP",    "Temp. consegna verificata, DDT controllato, fornitore qualificato"),
            ("CCP 2", "Stoccaggio freddo", "T° 0-4°C (frigo) | -18°C (surgelati) — monitoraggio continuo IoT"),
            ("CCP 3", "Scongelamento",     "In frigo a 4°C o in acqua corrente <21°C — mai a T° ambiente"),
            ("CCP 4", "Cottura",           "T° al cuore ≥75°C — verificata con termometro calibrato"),
            ("CCP 5", "Raffreddamento",    "Da 65°C a 10°C in ≤2h — abbattitore di temperatura"),
            ("CCP 6", "Mantenimento caldo","T° servizio ≥65°C — bainharie/lampade scaldavivande"),
            ("CCP 7", "Distribuzione",     "T° verificata al momento del servizio — max 1h esposizione"),
        ]

        header = [["CCP", "Fase", "Limite critico / Procedura", "Verificato", "NC"]]
        rows = [[c, f, d, "☑", "☐"] for c, f, d in ccp_items]
        data = header + rows

        col_w = [14*mm, 32*mm, 88*mm, 14*mm, 14*mm]
        t = Table(data, colWidths=col_w)
        t.setStyle(TableStyle([
            # Header
            ("BACKGROUND",   (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",    (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, 0), 8),
            # Body
            ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",     (0, 1), (-1, -1), 8),
            ("FONTNAME",     (0, 1), (0, -1),  "Helvetica-Bold"),
            ("TEXTCOLOR",    (0, 1), (0, -1),  C_ACCENT),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
            ("GRID",         (0, 0), (-1, -1), 0.3, C_BORDER),
            ("ALIGN",        (3, 0), (4, -1),  "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(t)

        story.append(Spacer(1, 2*mm))
        story.append(Paragraph(
            "☑ = Verificato conforme durante il mese  |  ☐ = Non verificato / Non applicabile  |  "
            "NC = Non conformità rilevata (vedere sezione 3)",
            self.sBodySm))
        return story

    # ── Sezione: Log Temperature ─────────────────────────────────────────────

    def _build_temperature_log(self) -> list:
        story = []
        story.append(PageBreak())
        story.append(Paragraph("  REGISTRO TEMPERATURE MENSILE — IoT HACCP", self.sSection))
        story.append(Spacer(1, 3*mm))

        if not self.data.lettori:
            story.append(Paragraph("Nessuna rilevazione nel periodo.", self.sBody))
            return story

        # Raggruppa per zona
        zone_map: dict[str, list[TemperatureReading]] = {}
        for r in self.data.lettori:
            zone_map.setdefault(r.zona, []).append(r)

        for zona, readings in zone_map.items():
            z_info = ZONE_RANGES.get(zona, (None, None, zona.replace("_", " ").title()))
            t_min, t_max, z_label = z_info
            range_str = f"{t_min}°C — {t_max}°C" if t_min is not None else "—"

            story.append(Paragraph(
                f"Zona: {z_label.upper()}  |  Range normativo: {range_str}  "
                f"|  Sensor: {readings[0].sensor_id}",
                ParagraphStyle("zone_hdr", fontName="Helvetica-Bold", fontSize=8,
                               textColor=C_ACCENT, spaceAfter=3, spaceBefore=8)))

            header = [["Data", "Ora", "T° (°C)", "Min", "Max", "Rilevazione", "Stato", "Azione correttiva"]]
            rows = []
            for r in readings:
                sev_label = _severity_label(r.severity)
                rows.append([
                    r.data, r.ora,
                    f"{r.temperatura:.1f}",
                    f"{r.temp_min:.0f}", f"{r.temp_max:.0f}",
                    r.rilevato_da.upper(),
                    sev_label,
                    r.azione_correttiva or "—",
                ])

            data = header + rows
            col_w = [16*mm, 12*mm, 16*mm, 10*mm, 10*mm, 20*mm, 18*mm, 54*mm]
            t = Table(data, colWidths=col_w)

            ts = TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, 0), 7),
                ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",      (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
                ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
                ("ALIGN",         (2, 0), (5, -1),  "CENTER"),
                ("ALIGN",         (6, 0), (6, -1),  "CENTER"),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING",   (0, 0), (-1, -1), 4),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("WORDWRAP",      (7, 1), (7, -1),  "ON"),
            ])
            # Colora cella stato per riga
            for i, r in enumerate(readings, start=1):
                col = _severity_color(r.severity)
                ts.add("TEXTCOLOR",   (6, i), (6, i), col)
                ts.add("FONTNAME",    (6, i), (6, i), "Helvetica-Bold")
                if r.alert:
                    ts.add("BACKGROUND", (6, i), (6, i), HexColor("#FFF8F0") if r.severity == "warning" else HexColor("#FFF0F0"))

            t.setStyle(ts)
            story.append(KeepTogether([t]))
            story.append(Spacer(1, 2*mm))

        return story

    # ── Sezione: Riepilogo NC ────────────────────────────────────────────────

    def _build_nc_summary(self) -> list:
        story = []
        alerts = [r for r in self.data.lettori if r.alert]

        story.append(PageBreak())
        story.append(Paragraph("  NON CONFORMITÀ E AZIONI CORRETTIVE", self.sSection))
        story.append(Spacer(1, 3*mm))

        if not alerts:
            story.append(Paragraph(
                "✓  Nessuna non conformità rilevata nel periodo. Tutti i CCP monitorati risultano conformi.",
                ParagraphStyle("ok_msg", fontName="Helvetica-Bold", fontSize=9,
                               textColor=C_SUCCESS, spaceAfter=6)))
        else:
            story.append(Paragraph(
                f"Totale non conformità: {len(alerts)}  |  "
                f"Warning: {sum(1 for a in alerts if a.severity == 'warning')}  |  "
                f"Critici: {sum(1 for a in alerts if a.severity == 'critical')}",
                ParagraphStyle("nc_hdr", fontName="Helvetica-Bold", fontSize=9,
                               textColor=C_DANGER, spaceAfter=6)))

            header = [["N.", "Data/Ora", "Zona", "T° rilevata", "Range", "Gravità", "Azione correttiva", "Chiusa"]]
            rows = []
            for i, r in enumerate(alerts, 1):
                z_info = ZONE_RANGES.get(r.zona, (r.temp_min, r.temp_max, r.zona))
                t_min, t_max, _ = z_info
                rows.append([
                    str(i),
                    f"{r.data}\n{r.ora}",
                    r.zona.replace("_", " ").title(),
                    f"{r.temperatura:.1f}°C",
                    f"{t_min}°C/{t_max}°C",
                    _severity_label(r.severity),
                    r.azione_correttiva or "Da completare",
                    "☐",
                ])

            data = header + rows
            col_w = [8*mm, 20*mm, 26*mm, 18*mm, 18*mm, 16*mm, 50*mm, 12*mm]
            t = Table(data, colWidths=col_w)
            ts = TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), C_DANGER),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, 0), 7),
                ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",      (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [HexColor("#FFF5F5"), C_WHITE]),
                ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
                ("ALIGN",         (7, 0), (7, -1), "CENTER"),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING",   (0, 0), (-1, -1), 4),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
            for i, r in enumerate(alerts, 1):
                col = _severity_color(r.severity)
                ts.add("TEXTCOLOR", (5, i), (5, i), col)
                ts.add("FONTNAME",  (5, i), (5, i), "Helvetica-Bold")
            t.setStyle(ts)
            story.append(t)

        return story

    # ── Sezione: Firma ───────────────────────────────────────────────────────

    def _build_signature(self) -> list:
        story = []
        story.append(Spacer(1, 10*mm))
        story.append(HRFlowable(width="100%", thickness=0.3, color=C_BORDER))
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph("DICHIARAZIONE DI CONFORMITÀ E FIRMA", ParagraphStyle(
            "sig_hdr", fontName="Helvetica-Bold", fontSize=9,
            textColor=C_PRIMARY, spaceAfter=4)))

        story.append(Paragraph(
            "Il sottoscritto Responsabile HACCP dichiara che il presente registro è stato "
            "compilato in conformità al Piano di Autocontrollo aziendale, al Reg. CE 852/2004 "
            "e alle disposizioni vigenti in materia di sicurezza alimentare (aggiornamento 2025).",
            self.sBody))
        story.append(Spacer(1, 8*mm))

        sig_data = [
            ["Responsabile HACCP", "Data", "Firma"],
            [self.data.responsabile_haccp,
             self.data.data_compilazione or datetime.now().strftime("%d/%m/%Y"),
             "_" * 35],
        ]
        sig_table = Table(sig_data, colWidths=[70*mm, 40*mm, 60*mm])
        sig_table.setStyle(TableStyle([
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  C_MUTED),
            ("GRID",         (0, 0), (-1, -1), 0.3, C_BORDER),
            ("BACKGROUND",   (0, 0), (-1, 0),  C_LIGHT),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ]))
        story.append(sig_table)
        story.append(Spacer(1, 4*mm))

        if self.data.note_generali:
            story.append(Paragraph(f"Note: {self.data.note_generali}", self.sBodySm))

        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(
            "Documento generato da BAD.S Unified Platform v2.0 — Conforme Reg. CE 852/2004 — "
            "Conservare per 5 anni ai sensi dell'art. 5 Reg. CE 852/2004",
            self.sFooter))
        return story

    # ── Genera PDF ───────────────────────────────────────────────────────────

    def generate(self) -> bytes:
        doc = SimpleDocTemplate(
            self.buf,
            pagesize=A4,
            topMargin=16*mm,
            bottomMargin=14*mm,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            title=f"Registro HACCP — {self.data.hotel_name} — {MESI_IT[self.data.mese]} {self.data.anno}",
            author="BAD.S Unified Platform",
            subject="Piano di Autocontrollo HACCP",
        )

        story = []
        story += self._build_cover()
        story += self._build_ccp_checklist()
        story += self._build_temperature_log()
        story += self._build_nc_summary()
        story += self._build_signature()

        doc.build(story, onFirstPage=self._on_page, onLaterPages=self._on_page)
        return self.buf.getvalue()


# ── Demo dati ────────────────────────────────────────────────────────────────

def make_demo_data() -> HACCPReportData:
    from random import uniform, choice, seed
    seed(42)

    readings = []
    days = list(range(1, 32))
    times = ["07:00", "13:00", "19:00"]

    zone_cfg = {
        "cella_frigo":      (0.0,  4.0,  "SENS-CF01"),
        "cella_surgelati":  (-22.0,-18.0,"SENS-CS01"),
        "frigo_bar":        (2.0,  6.0,  "SENS-FB01"),
        "zona_calda":       (65.0, 100.0,"SENS-ZC01"),
        "cantina":          (10.0, 18.0, "SENS-CT01"),
    }

    for d in days[:28]:
        for zona, (tmin, tmax, sensor) in zone_cfg.items():
            for ora in times:
                # Simula qualche anomalia
                noise = uniform(-0.3, 0.3)
                if zona == "cella_frigo" and d in [5, 12]:
                    t = uniform(5.5, 7.0)  # warning
                elif zona == "cella_surgelati" and d == 18:
                    t = uniform(-15.0, -13.0)  # critical
                else:
                    t = uniform(tmin + 0.5, tmax - 0.5) + noise

                alert = t < tmin or t > tmax
                if alert:
                    diff = max(abs(t - tmin), abs(t - tmax))
                    sev = "critical" if diff > 3 else "warning"
                else:
                    sev = "ok"

                readings.append(TemperatureReading(
                    data=f"{d:02d}/03/2026",
                    ora=ora,
                    zona=zona,
                    sensor_id=sensor,
                    temperatura=round(t, 1),
                    temp_min=tmin,
                    temp_max=tmax,
                    alert=alert,
                    severity=sev,
                    rilevato_da="iot",
                    operatore="Sistema IoT BAD.S",
                    azione_correttiva=(
                        "Verificato apertura porta — Temperatura ripristinata entro 45 min"
                        if sev == "warning" else
                        "Blocco cella attivato — Allertato responsabile — Prodotti trasferiti"
                        if sev == "critical" else ""
                    ),
                ))

    return HACCPReportData(
        hotel_name="Hotel BAD.S Demo",
        hotel_citta="Cagliari (CA)",
        hotel_piva="12345678901",
        responsabile_haccp="Dott.ssa Maria Rossi",
        anno=2026,
        mese=3,
        lettori=readings,
        note_generali="Formazione personale HACCP completata il 15/03/2026. Calibrazione termometri il 01/03/2026.",
        data_compilazione="31/03/2026",
    )


if __name__ == "__main__":
    data = make_demo_data()
    gen = HACCPPdfGenerator(data)
    pdf_bytes = gen.generate()
    with open("/tmp/registro_haccp_demo.pdf", "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF generato: {len(pdf_bytes)/1024:.1f} KB — {len(data.lettori)} rilevazioni — "
          f"{sum(1 for r in data.lettori if r.alert)} alert")
