import os
import io
import json
import logging
import google.generativeai as genai
from PIL import Image

logger = logging.getLogger(__name__)

def setup_ai():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("❌ GEMINI_API_KEY is missing in .env")
    genai.configure(api_key=api_key)

def generate_wallpaper_metadata(image_bytes: bytes) -> dict:
    """
    Takes an image, sends to Gemini Vision, and returns a dict with metadata.
    """
    try:
        # User API Key ONLY supports gemini-2.5-flash and gemini-2.0-flash variants
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Prepare the image
        img = Image.open(io.BytesIO(image_bytes))
        
        prompt = """
        Analyze this image and provide metadata for a premium wallpaper website.
        Generate the following in strict JSON format:
        {
            "title": "A short, catchy, SEO-friendly title (max 6 words)",
            "category": "Choose ONE from: Anime, Nature, Dark, Gaming, Minimal, Abstract, Sci-Fi, Aesthetic, Photography, Cars",
            "description": "A 1-2 sentence professional setup-worthy description.",
            "tags": ["tag1", "tag2", "tag3", "tag4"]
        }
        Absolutely DO NOT wrap the response in ```json ``` markdown. 
        Only output the raw parseable JSON curly brackets.
        """
        
        logger.info("🧠 Waiting for Gemini AI to analyze the image...")
        response = model.generate_content([prompt, img])
        response_text = response.text.strip()
        
        # Clean up markdown format if model disobeys instructions
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]

        metadata = json.loads(response_text.strip())
        logger.info(f"✅ AI Generated Metadata: {metadata.get('title')}")
        return metadata
    
    except Exception as e:
        logger.error(f"❌ AI Analysis Failed: {e}")
        # Fallback metadata so the upload still continues even if AI fails temporarily
        return {
            "title": f"Aesthetic Wallpaper {os.urandom(2).hex()}",
            "category": "Aesthetic",
            "description": f"A beautiful high-quality wallpaper. (AI Error: {str(e)[:50]}...)",
            "tags": ["wallpaper", "hd", "4k"]
        }
