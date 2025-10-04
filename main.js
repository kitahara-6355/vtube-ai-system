import * as base64js from "https://unpkg.com/base64-js@1.3.0/base64js.min.js";
import markdownit from 'https://unpkg.com/markdown-it@8.4.2/dist/markdown-it.min.js';
import { streamGemini } from './gemini-api.js';

// --- Text Generation --- //

let textGenerationForm = document.querySelector('#text-generation-form');
let promptInput = document.querySelector('input[name="prompt"]');
let textOutput = document.querySelector('.output'); // Renamed for clarity

textGenerationForm.onsubmit = async (ev) => {
  ev.preventDefault();
  textOutput.textContent = 'Generating...';

  try {
    // Load the image as a base64 string
    let imageUrl = textGenerationForm.elements.namedItem('chosen-image').value;
    let imageBase64 = await fetch(imageUrl)
      .then(r => r.arrayBuffer())
      .then(a => base64js.fromByteArray(new Uint8Array(a)));

    // Assemble the prompt by combining the text with the chosen image
    let contents = [
      {
        role: 'user',
        parts: [
          { inline_data: { mime_type: 'image/jpeg', data: imageBase64, } },
          { text: promptInput.value }
        ]
      }
    ];

    // Call the multimodal model, and get a stream of results
    let stream = streamGemini({
      model: 'gemini-2.5-pro-latest', // Keep using a text-focused model here
      contents,
    });

    // Read from the stream and interpret the output as markdown
    let buffer = [];
    let md = markdownit();
    for await (let chunk of stream) {
      buffer.push(chunk);
      textOutput.innerHTML = md.render(buffer.join(''));
    }
  } catch (e) {
    textOutput.innerHTML += '<hr>' + e;
  }
};


// --- Image Generation --- //

const imageGenerationForm = document.querySelector('#image-generation-form');
const imagePromptInput = document.querySelector('textarea[name="image-prompt"]');
const generatedImageContainer = document.querySelector('#generated-image-container');

imageGenerationForm.onsubmit = async (ev) => {
  ev.preventDefault();
  generatedImageContainer.innerHTML = '<p>生成中...</p>';

  try {
    const prompt = imagePromptInput.value;
    if (!prompt) {
        generatedImageContainer.innerHTML = '<p>プロンプトを入力してください。</p>';
        return;
    }

    const response = await fetch('/api/generate-image', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt: prompt }),
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || '画像の生成に失敗しました。');
    }

    const imageBlob = await response.blob();
    const imageUrl = URL.createObjectURL(imageBlob);

    const imgElement = document.createElement('img');
    imgElement.src = imageUrl;
    imgElement.alt = prompt; // Use the prompt as the alt text

    generatedImageContainer.innerHTML = ''; // Clear the "Generating..." message
    generatedImageContainer.appendChild(imgElement);

  } catch (e) {
    generatedImageContainer.innerHTML = `<p>エラー: ${e.message}</p>`;
  }
};
