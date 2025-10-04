
import json
import os
import io

import google.generativeai as genai
from google.generativeai import types
from flask import Flask, jsonify, request, send_file
from google.cloud import secretmanager
from PIL import Image
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def access_secret_version(project_id, secret_id, version_id="latest"):
    """
    Access the payload for the given secret version if one exists.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

# --- Configuration --- #
PROJECT_ID = os.environ.get("PROJECT_ID")

# API key for the text generation model (Gemini).
API_KEY = access_secret_version(PROJECT_ID, "GEMINI_API_KEY") if PROJECT_ID else None

# Model name for the image generation model (Imagen).
IMAGEN_MODEL_NAME = os.environ.get("IMAGEN_MODEL_NAME", "imagen-3.0-generate-002")

if API_KEY:
    genai.configure(api_key=API_KEY)

# Let Flask handle everything by pointing the static folder to the root URL.
app = Flask(__name__, static_url_path='', static_folder='web')


@app.route("/")
def index():
    # Serve the main entry point of the app, index.html.
    return app.send_static_file('index.html')


@app.route("/api/generate", methods=["POST"])
def generate_api():
    if not API_KEY:
        return jsonify({
            "error": "API key not configured. Set the PROJECT_ID environment variable."
        })
    try:
        req_body = request.get_json()
        contents = req_body.get("contents")
        model_name = req_body.get("model", "gemini-1.5-flash")
        model = genai.GenerativeModel(model_name=model_name)
        response = model.generate_content(contents, stream=True)

        def stream():
            for chunk in response:
                if chunk.text:
                    yield f"data: {json.dumps({'text': chunk.text})}\n\n"

        return stream(), {"Content-Type": "text/event-stream"}

    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/generate-image", methods=["POST"])
def generate_image_api():
    if not PROJECT_ID:
        return jsonify({
            "error": "PROJECT_ID environment variable not set."
        }), 500
    try:
        req_body = request.get_json()
        prompt = req_body.get("prompt")
        if not prompt:
            return jsonify({"error": "Prompt is required"}), 400

        # Placeholder for image generation logic
        return jsonify({"message": "Image generation endpoint is not implemented yet."})


    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Disable the reloader to prevent infinite loops in this specific IDE environment.
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
