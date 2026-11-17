// Shared utility functions for Manga Translator extension

function hashObject(obj) {
  const str = JSON.stringify(obj);
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash).toString(16);
}

async function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const base64 = reader.result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function base64ToBlob(base64, mimeType = 'image/png') {
  if (!base64 || typeof base64 !== 'string') {
    throw new Error('Invalid base64 input: empty or not a string');
  }
  const base64Data = base64.replace(/^data:[^,]*;base64,/, '');
  if (base64Data.length === 0) {
    throw new Error('Invalid base64 input: empty after prefix removal');
  }
  if (!/^[A-Za-z0-9+/=]+$/.test(base64Data.substring(0, Math.min(100, base64Data.length)))) {
    throw new Error('Invalid base64 encoding: contains invalid characters');
  }
  try {
    const byteCharacters = atob(base64Data);
    const byteNumbers = new Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    return new Blob([new Uint8Array(byteNumbers)], { type: mimeType });
  } catch (e) {
    throw new Error(`Base64 decode failed: ${e.message}`);
  }
}

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { hashObject, blobToBase64, base64ToBlob };
}
