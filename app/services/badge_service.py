"""
Badge generation service
"""

import base64
import os
import time
from io import BytesIO
from typing import Dict, Any

from PIL import Image, ImageDraw

from app.core.composer import render_from_spec
from app.core.utils.text import load_font
from app.models.responses import BadgeResponse, BadgeData
from app.core.logging_config import get_logger, log_badge_generation

# Use main API logger
logger = get_logger("badge_service")

# Text styling defaults for template-based badge generation
TEMPLATE_TEXT_DEFAULTS = {
    "title": {
        "font_path": "assets/fonts/ArialBold.ttf",
        "font_size": 48,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
    },
    "subtitle": {
        "font_path": "assets/fonts/Arial.ttf",
        "font_size": 38,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 1,
    },
    "line_gap": 20,
    "padding": 80,  # Horizontal padding from edges
}


def _wrap_text(draw, text, font, max_width):
    """
    Wrap text to fit within max_width

    Args:
        draw: ImageDraw instance
        text: Text to wrap
        font: Font to use for measurement
        max_width: Maximum width in pixels

    Returns:
        List of text lines that fit within max_width
    """
    if not text:
        return []

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]

        if line_width <= max_width or not current_line:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines

class BadgeService:
    """Service for generating badge images"""

    async def generate_badge(self, config: Dict[str, Any]) -> BadgeResponse:
        """
        Generate a badge image from configuration

        Args:
            config: Badge configuration dictionary

        Returns:
            BadgeResponse with base64 encoded image
        """
        start_time = time.time()

        try:
            logger.info("Starting badge generation")

            # Add fixed canvas dimensions
            if "canvas" not in config:
                config["canvas"] = {}
            config["canvas"]["width"] = 600
            config["canvas"]["height"] = 600

            # Preserve scale_factor if already set, otherwise it will be extracted from canvas config in render_from_spec

            # Add default background layer only if one doesn't exist
            has_background = any(layer.get("type") == "BackgroundLayer" for layer in config.get("layers", []))
            if not has_background:
                config["layers"].insert(0, {
                    "type": "BackgroundLayer",
                    "mode": "solid",
                    "color": "#FFFFFF00",
                    "z": 0
                })

            # Generate badge using composer
            image = render_from_spec(config)

            if image is None:
                raise ValueError("Failed to generate badge image")

            # Convert PIL Image to base64
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            buffer.seek(0)

            # Encode to base64
            img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

            generation_time = time.time() - start_time

            # Log successful generation
            log_badge_generation(config, success=True, generation_time=generation_time)
            logger.info(f"Badge generated successfully in {generation_time:.3f}s")

            # Create response
            return BadgeResponse(
                success=True,
                message="Badge generated successfully",
                data=BadgeData(
                    base64=f"data:image/png;base64,{img_base64}"
                    #filename="badge.png",
                    #mimeType="image/png"
                ),
                config=config
            )

        except Exception as e:
            generation_time = time.time() - start_time
            error_msg = str(e)

            # Log failed generation
            log_badge_generation(config, success=False, error=error_msg, generation_time=generation_time)
            logger.error(f"Badge generation failed after {generation_time:.3f}s: {error_msg}")
            raise

    async def generate_from_template(
        self,
        template_path: str,
        title: str,
        subtitle: str,
        scale_factor: float = 1.0
    ) -> BadgeResponse:
        """
        Generate a badge by overlaying text on a template image

        Args:
            template_path: Relative path to the template PNG file
            title: Title text to overlay
            subtitle: Subtitle text to overlay
            scale_factor: Scale factor for output (1.0-3.0)

        Returns:
            BadgeResponse with base64 encoded image
        """
        start_time = time.time()

        try:
            logger.info(f"Starting template badge generation with title: '{title}', subtitle: '{subtitle}'")

            # Resolve full path to template
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(script_dir))
            full_template_path = os.path.join(project_root, template_path)

            # Load template image
            template = Image.open(full_template_path).convert("RGBA")
            original_width, original_height = template.size

            # Scale the template if needed
            if scale_factor != 1.0:
                new_width = int(original_width * scale_factor)
                new_height = int(original_height * scale_factor)
                template = template.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Create drawing context
            draw = ImageDraw.Draw(template)

            # Load fonts with scaling
            title_font_size = int(TEMPLATE_TEXT_DEFAULTS["title"]["font_size"] * scale_factor)
            subtitle_font_size = int(TEMPLATE_TEXT_DEFAULTS["subtitle"]["font_size"] * scale_factor)
            line_gap = int(TEMPLATE_TEXT_DEFAULTS["line_gap"] * scale_factor)
            padding = int(TEMPLATE_TEXT_DEFAULTS["padding"] * scale_factor)

            title_font_path = os.path.join(project_root, TEMPLATE_TEXT_DEFAULTS["title"]["font_path"])
            subtitle_font_path = os.path.join(project_root, TEMPLATE_TEXT_DEFAULTS["subtitle"]["font_path"])

            title_font = load_font(title_font_path, title_font_size)
            subtitle_font = load_font(subtitle_font_path, subtitle_font_size)

            # Get canvas dimensions
            canvas_width, canvas_height = template.size

            # Calculate max width for text wrapping (canvas width minus padding on both sides)
            max_text_width = canvas_width - (padding * 2)

            # Wrap title and subtitle text
            title_lines = _wrap_text(draw, title, title_font, max_text_width)
            subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, max_text_width)

            # Calculate heights for each line
            def get_line_height(font):
                bbox = font.getbbox("Ay")  # Use typical characters for height
                return bbox[3] - bbox[1]

            title_line_height = get_line_height(title_font)
            subtitle_line_height = get_line_height(subtitle_font)

            # Calculate total text block height
            title_block_height = len(title_lines) * title_line_height + (len(title_lines) - 1) * (line_gap // 2) if title_lines else 0
            subtitle_block_height = len(subtitle_lines) * subtitle_line_height + (len(subtitle_lines) - 1) * (line_gap // 2) if subtitle_lines else 0
            total_text_height = title_block_height + line_gap + subtitle_block_height

            # Calculate starting Y position to center the entire text block
            start_y = (canvas_height - total_text_height) // 2

            # Draw title lines (centered)
            title_stroke_width = int(TEMPLATE_TEXT_DEFAULTS["title"]["stroke_width"] * scale_factor)
            current_y = start_y
            for line in title_lines:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                line_width = bbox[2] - bbox[0]
                line_x = (canvas_width - line_width) // 2
                draw.text(
                    (line_x, current_y),
                    line,
                    font=title_font,
                    fill=TEMPLATE_TEXT_DEFAULTS["title"]["color"],
                    stroke_width=title_stroke_width,
                    stroke_fill=TEMPLATE_TEXT_DEFAULTS["title"]["stroke_color"]
                )
                current_y += title_line_height + (line_gap // 2)

            # Add gap between title and subtitle
            current_y = start_y + title_block_height + line_gap

            # Draw subtitle lines (centered)
            subtitle_stroke_width = int(TEMPLATE_TEXT_DEFAULTS["subtitle"]["stroke_width"] * scale_factor)
            for line in subtitle_lines:
                bbox = draw.textbbox((0, 0), line, font=subtitle_font)
                line_width = bbox[2] - bbox[0]
                line_x = (canvas_width - line_width) // 2
                draw.text(
                    (line_x, current_y),
                    line,
                    font=subtitle_font,
                    fill=TEMPLATE_TEXT_DEFAULTS["subtitle"]["color"],
                    stroke_width=subtitle_stroke_width,
                    stroke_fill=TEMPLATE_TEXT_DEFAULTS["subtitle"]["stroke_color"]
                )
                current_y += subtitle_line_height + (line_gap // 2)

            # Convert to base64
            buffer = BytesIO()
            template.save(buffer, format='PNG')
            buffer.seek(0)
            img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

            generation_time = time.time() - start_time
            logger.info(f"Template badge generated successfully in {generation_time:.3f}s")

            # Return response
            config = {
                "title": title,
                "subtitle": subtitle,
                "scale_factor": scale_factor
            }

            return BadgeResponse(
                success=True,
                message="Badge generated successfully from template",
                data=BadgeData(base64=f"data:image/png;base64,{img_base64}"),
                config=config
            )

        except Exception as e:
            generation_time = time.time() - start_time
            error_msg = str(e)
            logger.error(f"Template badge generation failed after {generation_time:.3f}s: {error_msg}")
            raise