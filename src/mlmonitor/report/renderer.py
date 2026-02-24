"""
PDFRenderer — Jinja2 HTML → weasyprint PDF.
"""

from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _nl2br(value: str) -> Markup:
    """Convierte saltos de línea en etiquetas HTML para el PDF."""
    escaped = escape(value)
    result = escaped.replace("\n\n", Markup("</p><p>"))
    result = result.replace("\n", Markup("<br>"))
    return Markup(f"<p>{result}</p>")


class PDFRenderer:
    """Renderiza el reporte HTML y lo convierte a PDF con weasyprint."""

    def __init__(self, output_dir: str | Path | None = None):
        self.output_dir = Path(output_dir) if output_dir else Path("artifacts/reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self.jinja_env.filters["nl2br"] = _nl2br

    def render_html(
        self,
        context: AnalysisContext,
        result: AnalysisResult | None,
        generation_date: date | None = None,
    ) -> str:
        """Renderiza el HTML del reporte y retorna el string."""
        if generation_date is None:
            generation_date = date.today()

        template = self.jinja_env.get_template("fleet_report.html")

        fleet_narrative = result.fleet_narrative if result else ""
        segment_narratives = result.segment_narratives if result else {}
        recommended_actions = result.recommended_actions if result else {}

        html = template.render(
            context=context,
            fleet_narrative=fleet_narrative,
            segment_narratives=segment_narratives,
            recommended_actions=recommended_actions,
            generation_date=generation_date.isoformat(),
        )
        return html

    def render_pdf(
        self,
        context: AnalysisContext,
        result: AnalysisResult | None,
        generation_date: date | None = None,
        filename: str | None = None,
    ) -> Path:
        """
        Renderiza el reporte como PDF y lo guarda en output_dir.
        Retorna la ruta del archivo generado.
        """
        if generation_date is None:
            generation_date = date.today()

        if filename is None:
            filename = f"mlmonitor_{generation_date.isoformat()}.pdf"

        output_path = self.output_dir / filename

        html_content = self.render_html(context, result, generation_date)

        try:
            from weasyprint import HTML, CSS

            HTML(string=html_content, base_url=str(TEMPLATES_DIR)).write_pdf(
                str(output_path)
            )
        except ImportError:
            # Fallback: guardar HTML si weasyprint no está disponible
            html_path = output_path.with_suffix(".html")
            html_path.write_text(html_content, encoding="utf-8")
            print(
                f"[PDFRenderer] weasyprint no disponible. "
                f"HTML guardado en: {html_path}"
            )
            return html_path

        print(f"[PDFRenderer] PDF generado: {output_path}")
        return output_path
