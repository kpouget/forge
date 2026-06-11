"""Core visualization utilities for Caliper reports and analysis."""

from __future__ import annotations

import base64
from pathlib import Path


def save_figure(
    fig,
    output_dir: Path,
    filename: str,
    as_image: bool = True,
    report_number: int | None = None,
    width: int = 800,
    height: int = 500,
) -> str | None:
    """
    Save a plotly figure as either an image or HTML file with optional report numbering.

    Args:
        fig: Plotly figure object
        output_dir: Directory to save the file
        filename: Base filename (without extension)
        as_image: If True, save as PNG; if False, save as HTML
        report_number: Optional report number for file naming (e.g., 0 for "report_00_")
        width: Image width in pixels (for PNG output)
        height: Image height in pixels (for PNG output)

    Returns:
        Path to the saved file, or None if failed
    """
    try:
        # Add report number prefix if provided
        if report_number is not None:
            final_filename = f"report_{report_number:02d}_{filename}"
        else:
            final_filename = filename

        if as_image:
            print(f"💾 Saving {final_filename} as PNG image...")
            output_file = output_dir / f"{final_filename}.png"
            fig.write_image(output_file, width=width, height=height, scale=2)
        else:
            print(f"💾 Saving {final_filename} as full-page interactive HTML...")
            output_file = output_dir / f"{final_filename}.html"

            # Configure the figure for full-page display
            fig.update_layout(
                autosize=True,
                margin=dict(l=20, r=20, t=60, b=40),
            )

            # Save with full-page configuration
            fig.write_html(
                output_file,
                include_plotlyjs="cdn",
                config={
                    "displayModeBar": True,
                    "displaylogo": False,
                    "modeBarButtonsToRemove": ["pan2d", "lasso2d"],
                    "responsive": True,
                },
                div_id="plotly-div",
                full_html=True,
                include_mathjax=False,
            )

            # Make it full page
            _make_html_full_page(output_file)

        print(f"✅ {final_filename} saved successfully")
        return str(output_file)

    except Exception as e:
        final_filename = (
            f"report_{report_number:02d}_{filename}" if report_number is not None else filename
        )
        print(f"❌ Failed to save figure {final_filename}: {e}")
        return None


def _make_html_full_page(html_file_path: str) -> None:
    """
    Modify an HTML file to make the plot full page by adding custom CSS.

    Args:
        html_file_path: Path to the HTML file to modify
    """
    try:
        with open(html_file_path, encoding="utf-8") as f:
            content = f.read()

        # Insert full-page CSS styles
        full_page_css = """
    <style>
        html, body {
            height: 100%;
            margin: 0;
            padding: 0;
            overflow: hidden;
        }
        #plotly-div {
            height: 100vh !important;
            width: 100vw !important;
        }
        .plotly-graph-div {
            height: 100vh !important;
            width: 100vw !important;
        }
    </style>
"""

        # Insert the CSS before the closing </head> tag
        content = content.replace("</head>", f"{full_page_css}</head>")

        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(content)

    except Exception as e:
        print(f"⚠️  Warning: Failed to make HTML full page: {e}")


def write_full_page_html(fig, output_file_path: str, title: str = "Plot") -> bool:
    """
    Save a Plotly figure as a full-page HTML file.

    Args:
        fig: Plotly figure object
        output_file_path: Path where to save the HTML file
        title: Title for the HTML page

    Returns:
        True if successful, False otherwise
    """
    try:
        # Configure the figure for full-page display
        fig.update_layout(
            autosize=True,
            margin=dict(l=20, r=20, t=60, b=40),
            title_text=title if title != "Plot" else None,
        )

        # Save with full-page configuration
        fig.write_html(
            output_file_path,
            include_plotlyjs="cdn",
            config={
                "displayModeBar": True,
                "displaylogo": False,
                "modeBarButtonsToRemove": ["pan2d", "lasso2d"],
                "responsive": True,
            },
            div_id="plotly-div",
            full_html=True,
            include_mathjax=False,
        )

        # Make it full page
        _make_html_full_page(output_file_path)
        return True

    except Exception as e:
        print(f"❌ Failed to save full-page HTML: {e}")
        return False


def figure_to_base64(
    fig, width: int = 800, height: int = 500, plot_name: str = "plot"
) -> str | None:
    """
    Convert a plotly figure to base64-encoded PNG for embedding in HTML.

    Args:
        fig: Plotly figure object
        width: Image width in pixels
        height: Image height in pixels
        plot_name: Name of the plot for logging

    Returns:
        Base64-encoded image string with data URI prefix, or None if failed
    """
    try:
        print(f"🖼️  Converting {plot_name} to high-quality PNG ({width}x{height})...")

        # Convert figure to PNG bytes
        img_bytes = fig.to_image(format="png", width=width, height=height, scale=2)

        print(f"📦 Encoding {plot_name} as base64 for HTML embedding...")
        # Encode as base64
        img_base64 = base64.b64encode(img_bytes).decode()

        print(f"✅ {plot_name} image ready ({len(img_base64) // 1024}KB)")
        return f"data:image/png;base64,{img_base64}"

    except Exception as e:
        print(f"❌ Failed to convert {plot_name} to base64: {e}")
        return None


def create_report_filename(
    base_name: str,
    report_number: int | None = None,
    report_title: str | None = None,
    extension: str = "html",
) -> str:
    """
    Create a standardized filename for Caliper reports.

    Args:
        base_name: Base filename (e.g., "performance_analysis")
        report_number: Optional report number (e.g., 0 for "Report 00:")
        report_title: Optional human-readable title for the report
        extension: File extension (without dot)

    Returns:
        Formatted filename following Caliper conventions

    Examples:
        >>> create_report_filename("performance_analysis", 0, "GuideLLM Performance Analysis")
        "report_00_guidellm_performance_analysis.html"

        >>> create_report_filename("summary", 5, "Baseline Comparisons")
        "report_05_baseline_comparisons.html"

        >>> create_report_filename("analysis")  # No numbering
        "analysis.html"
    """
    if report_number is not None:
        # Use report title if provided, otherwise base name
        if report_title:
            # Convert title to filename-safe format
            safe_title = report_title.lower().replace(" ", "_").replace(":", "").replace("-", "_")
            filename = f"report_{report_number:02d}_{safe_title}"
        else:
            filename = f"report_{report_number:02d}_{base_name}"
    else:
        filename = base_name

    return f"{filename}.{extension}"


def create_report_title_display(base_title: str, report_number: int | None = None) -> str:
    """
    Create a standardized display title for Caliper reports.

    Args:
        base_title: Base title (e.g., "GuideLLM Performance Analysis")
        report_number: Optional report number

    Returns:
        Formatted display title

    Examples:
        >>> create_report_title_display("GuideLLM Performance Analysis", 0)
        "Report 00: GuideLLM Performance Analysis"

        >>> create_report_title_display("Baseline Comparisons", 1)
        "Report 01: Baseline Comparisons"

        >>> create_report_title_display("Summary")  # No numbering
        "Summary"
    """
    if report_number is not None:
        return f"Report {report_number:02d}: {base_title}"
    else:
        return base_title
