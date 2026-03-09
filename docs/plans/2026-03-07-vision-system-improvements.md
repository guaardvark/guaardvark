# Vision System Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve the chat vision system with auto-detection, unified chat integration, conversation context, inline streaming display, image generation tool, and multi-image support.

**Architecture:** Six incremental improvements that build on each other. Each task is independent enough to commit separately but they share a dependency chain: auto-detection (foundation) -> unified chat vision (core) -> conversation context -> streaming display -> image gen tool -> multi-image.

**Tech Stack:** Ollama vision API, Flask/Socket.IO, React, existing BaseTool framework, existing batch_image_generator.

---

## Task 1: Vision Model Auto-Detection

**Goal:** Replace hardcoded vision model lists with metadata-based detection from Ollama.

**Files:**
- Modify: `backend/utils/chat_utils.py` (lines 27-94)
- Modify: `backend/services/vision_chat_service.py` (lines 59-143)

**Step 1: Enhance `_update_vision_models_cache()` in `chat_utils.py`**

Add Ollama `/api/show` metadata check. After the existing pattern-based detection (line 69), also query each model's metadata for vision capability indicators (projector layers, vision encoder, clip).

```python
# In _update_vision_models_cache(), after pattern matching loop (around line 72):
# Also check model metadata for vision capabilities
def _check_model_metadata_for_vision(model_name: str) -> bool:
    """Check Ollama model metadata for vision capability indicators."""
    try:
        import requests
        from backend.config import OLLAMA_BASE_URL
        base_url = OLLAMA_BASE_URL if hasattr(__import__('backend.config', fromlist=['OLLAMA_BASE_URL']), 'OLLAMA_BASE_URL') else "http://localhost:11434"
        resp = requests.post(f"{base_url}/api/show", json={"name": model_name}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            modelfile = data.get("modelfile", "").lower()
            template = data.get("template", "").lower()
            # Check for vision-related architecture indicators
            if any(indicator in modelfile for indicator in ["projector", "vision", "clip", "image"]):
                return True
            # Check parameters for vision-related settings
            params = data.get("parameters", "")
            if "image" in str(params).lower():
                return True
    except Exception:
        pass
    return False
```

Integrate this into the `_update_vision_models_cache()` function: for models that don't match patterns, also check metadata.

**Step 2: Update `_is_vision_capable_by_name()` pattern list**

Add newer model patterns to `VISION_MODEL_PATTERNS` (line 27-31):
```python
VISION_MODEL_PATTERNS = [
    "vision", "llava", "gpt-4", "gpt4", "gpt-4o",
    "qwen.*vl", "minicpm-v", "moondream", "bakllava",
    "llama.*vision", "granite.*vision", "gemma.*vision",
    "cogvlm", "internvl", "phi.*vision", "deepseek.*vl",
    "pixtral", "molmo",
]
```

**Step 3: Remove hardcoded list from `vision_chat_service.py`**

In `VisionChatService.__init__()` (line 62), remove the hardcoded `self.fallback_vision_models` list. Instead, have `_get_available_models()` delegate entirely to `chat_utils.is_vision_model()` and `_update_vision_models_cache()` for discovery.

```python
# Replace self.fallback_vision_models with dynamic lookup
def _get_vision_model(self) -> Optional[str]:
    """Get best available vision model using centralized detection."""
    from backend.utils.chat_utils import is_vision_model, get_vision_models

    # First check if current active model is vision-capable
    try:
        from backend.utils.llm_service import get_active_model_name
        current_model = get_active_model_name()
        if current_model and is_vision_model(current_model):
            return current_model
    except Exception:
        pass

    # Fall back to any available vision model
    vision_models = get_vision_models()  # New function to add to chat_utils
    return vision_models[0] if vision_models else None
```

Add `get_vision_models()` to `chat_utils.py`:
```python
def get_vision_models() -> List[str]:
    """Return list of available vision-capable models."""
    _update_vision_models_cache()
    return list(_vision_models_cache["models"])
```

**Step 4: Verify & commit**

Run: `python3 -c "from backend.utils.chat_utils import is_vision_model, get_vision_models; print(get_vision_models())"`

Commit: `git commit -m "feat: vision model auto-detection via Ollama metadata"`

---

## Task 2: Unified Chat Vision Support

**Goal:** Enable image analysis through the main unified chat path (ReACT loop with tools + RAG), instead of the separate `/vision/analyze` endpoint.

**Files:**
- Modify: `backend/api/unified_chat_api.py` (lines 20-35)
- Modify: `backend/services/unified_chat_engine.py` (lines 295-370, 695-730, 957-991)
- Modify: `frontend/src/api/unifiedChatService.js` (lines 28-45)
- Modify: `frontend/src/components/chat/ChatInput.jsx` (lines 774-832, 854-860)
- Modify: `frontend/src/pages/ChatPage.jsx` (around line 1596-1614)

**Step 1: Accept image in unified chat API**

In `unified_chat_api.py`, update the POST handler to accept an optional `image` field (base64-encoded):

```python
# After line 29: message = data.get("message", "").strip()
image_data = data.get("image")  # Optional base64-encoded image

# Pass to engine.chat()
# After line 74:
engine.chat(session_id, message, options, emit_fn, app=app, image_data=image_data)
```

**Step 2: Update `UnifiedChatEngine.chat()` signature**

In `unified_chat_engine.py`, add `image_data` parameter to `chat()` and `_run_chat()`:

```python
def chat(self, session_id: str, message: str, options: Dict[str, Any],
         emit_fn: Callable, app=None, image_data: str = None) -> Dict[str, Any]:
    # ... pass image_data to _run_chat()
```

**Step 3: Include image in Ollama messages**

In `_run_chat()` message building (around line 365-367), when `image_data` is present:

```python
# Build user message with optional image
user_msg = {"role": "user", "content": user_content}
if image_data:
    import base64
    # image_data is base64 string from frontend
    user_msg["images"] = [image_data]  # Ollama accepts base64 strings
ollama_messages.append(user_msg)
```

**Step 4: Save image metadata to DB**

In the `_save_message()` call for the user message (around line 370):

```python
# Save user message with image indicator
extra = None
if image_data:
    extra = {"hasImage": True, "messageType": "image_upload"}
self._save_message(session_id, "user", message, extra_data=extra)
```

**Step 5: Update frontend `unifiedChatService.sendMessage()`**

In `unifiedChatService.js`, accept optional image parameter:

```javascript
async sendMessage(sessionId, message, options = {}, imageBase64 = null) {
    const body = {
      session_id: sessionId,
      message,
      options,
    };
    if (imageBase64) {
      body.image = imageBase64;
    }
    const response = await fetch(`${API_BASE}/api/chat/unified`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    // ... rest unchanged
}
```

**Step 6: Route image paste through unified chat when enabled**

In `ChatInput.jsx`, modify `analyzeImage()` (line 774) and `handleSend` (line 854):

When unified chat is enabled (check via prop or localStorage `use_unified_chat`), instead of POSTing to `/api/enhanced-chat/vision/analyze`, convert the image to base64 and call `onSendMessage()` with the image data for the unified chat path.

```javascript
const analyzeImage = async () => {
    if (!imageState.selectedImage) return;

    // Convert image to base64
    const reader = new FileReader();
    reader.onload = (e) => {
        const base64 = e.target.result.split(',')[1]; // Strip data:image/...;base64, prefix
        const messageText = inputText || `Describe this image: ${imageState.selectedImage.name}`;
        onSendMessage(messageText, null, {
            isImageAnalysis: true,
            imageBase64: base64,
            imageFileName: imageState.selectedImage.name,
        });
        clearImage();
        setInputText("");
    };
    reader.readAsDataURL(imageState.selectedImage);
};
```

In `ChatPage.jsx`, update the unified chat send path (around line 1596-1614) to pass image data:

```javascript
// In the unified chat flow section:
if (useUnifiedChat && unifiedChatService) {
    // Check for image analysis with base64 data
    const imageBase64 = voiceOptions?.imageBase64 || null;
    const ackResult = await unifiedChatService.sendMessage(
        sessionId, modifiedInputText, { use_rag: true, chat_mode: chatMode }, imageBase64
    );
}
```

Also: when image is sent through unified chat, save the image file to `data/uploads/chat_images/` for persistence (so it can be displayed in history). This can be done either frontend-side (upload first, get URL) or backend-side (save base64 in the API handler). Backend-side is cleaner:

In `unified_chat_api.py`, after receiving `image_data`:
```python
if image_data:
    # Save image for chat history display
    import os, uuid, base64
    from backend.config import UPLOAD_DIR
    img_dir = os.path.join(UPLOAD_DIR, "chat_images")
    os.makedirs(img_dir, exist_ok=True)
    fname = f"chat_image_{uuid.uuid4().hex[:8]}.png"
    with open(os.path.join(img_dir, fname), "wb") as f:
        f.write(base64.b64decode(image_data))
    image_url = f"/api/enhanced-chat/vision/image/{fname}"
    options["_image_url"] = image_url  # Pass through to engine for saving
```

**Step 7: Add user message with image to chat feed immediately**

In `ChatPage.jsx`, in the `processMessage` function, add the user message with image preview BEFORE sending to unified chat. The image preview is already in `imageState.imagePreview` (base64 data URL). Add it to the user message object:

```javascript
// When creating user message in processMessage (around line 1170-1187):
if (voiceOptions?.isImageAnalysis) {
    userMessage.imageUrl = voiceOptions.imagePreview; // base64 data URL for immediate display
    userMessage.imageFileName = voiceOptions.imageFileName;
    userMessage.messageType = "image_upload";
}
```

**Step 8: Keep legacy vision/analyze as fallback**

Don't remove the existing `/api/enhanced-chat/vision/analyze` endpoint. It remains the fallback when unified chat is disabled. The ChatInput check becomes:

```javascript
const handleSend = async () => {
    if (imageState.selectedImage) {
        // Use unified chat path if enabled, otherwise legacy vision endpoint
        const useUnified = localStorage.getItem("use_unified_chat") !== "false";
        if (useUnified) {
            await analyzeImageUnified(); // New function using base64 + onSendMessage
        } else {
            await analyzeImageLegacy(); // Current analyzeImage() renamed
        }
        return;
    }
    // ... rest of handleSend
};
```

**Step 9: Verify & commit**

Test: Paste image in chat with unified chat ON -> should stream through ReACT loop.
Test: Paste image with unified chat OFF -> should use legacy vision/analyze endpoint.

Commit: `git commit -m "feat: unified chat vision support - images through ReACT loop"`

---

## Task 3: Image in Conversation Context

**Goal:** Include image context markers in conversation history so the LLM knows when images were discussed in previous turns.

**Files:**
- Modify: `backend/services/unified_chat_engine.py` (lines 732-757)

**Step 1: Update `_load_history()` to include image markers**

```python
def _load_history(self, session_id: str, limit: int = 20) -> List[Dict[str, str]]:
    """Load conversation history from DB (thread-safe with app context)."""
    try:
        from backend.models import LLMSession, LLMMessage, db
        ctx = self.app.app_context() if self.app else None
        if ctx:
            ctx.push()
        try:
            session = db.session.get(LLMSession, session_id)
            if not session:
                return []
            messages = (
                LLMMessage.query
                .filter_by(session_id=session_id)
                .order_by(LLMMessage.timestamp.desc())
                .limit(limit)
                .all()
            )
            messages.reverse()
            result = []
            for m in messages:
                content = m.content
                # Add image context marker if message had an image
                if m.extra_data and isinstance(m.extra_data, dict):
                    if m.extra_data.get("hasImage") or m.extra_data.get("messageType") == "image_upload":
                        fname = m.extra_data.get("imageFileName", "unknown")
                        content = f"[User attached an image: {fname}] {content}"
                result.append({"role": m.role, "content": content})
            return result
        finally:
            if ctx:
                ctx.pop()
    except Exception as e:
        logger.warning(f"Failed to load history for {session_id}: {e}")
        return []
```

**Step 2: Verify & commit**

Test: Send image, get response, then ask "what was in the image?" -> LLM should reference the image context marker.

Commit: `git commit -m "feat: image context markers in conversation history"`

---

## Task 4: Inline Image Display in Streaming

**Goal:** Add a `chat:image` Socket.IO event so the streaming message component can display images inline (for image gen tool results and vision responses).

**Files:**
- Modify: `frontend/src/components/chat/StreamingMessage.jsx` (lines 37-126, 159-292)
- Modify: `frontend/src/api/unifiedChatService.js` (lines 51-74)

**Step 1: Add `chat:image` event handler to `unifiedChatService.js`**

```javascript
// After onError (around line 73):
onImage(callback) { this._on("chat:image", callback); }
```

**Step 2: Add image state and handler to `StreamingMessage.jsx`**

```javascript
// Add to state (around line 30):
const [images, setImages] = useState([]);

// Add handler (around line 106):
const handleImage = useCallback((data) => {
    setImages(prev => [...prev, {
        url: data.image_url,
        alt: data.alt || "Generated image",
        caption: data.caption || "",
    }]);
}, []);

// Register listener (around line 120):
chatService.onImage(handleImage);
```

**Step 3: Render images inline in the component**

```jsx
{/* After tool call cards, before text content (around line 200): */}
{images.length > 0 && (
    <Box sx={{ mb: 1 }}>
        {images.map((img, idx) => (
            <Box key={idx} sx={{ mb: 1 }}>
                <CardMedia
                    component="img"
                    sx={{
                        maxWidth: 400,
                        maxHeight: 300,
                        width: 'auto',
                        height: 'auto',
                        borderRadius: 1,
                        border: '1px solid',
                        borderColor: 'divider',
                        objectFit: 'contain'
                    }}
                    image={img.url}
                    alt={img.alt}
                />
                {img.caption && (
                    <Typography variant="caption" sx={{ mt: 0.5, display: 'block' }}>
                        {img.caption}
                    </Typography>
                )}
            </Box>
        ))}
    </Box>
)}
```

Add `CardMedia, Typography` to MUI imports at top of file. Add `Box` if not already imported.

**Step 4: Pass images to completed message**

In `handleComplete` callback (around line 85-104), include the images array so they persist after streaming ends:

```javascript
const handleComplete = useCallback((data) => {
    // ... existing code
    onComplete({
        // ... existing fields
        images: images,  // Pass accumulated images
    });
}, [images, onComplete]);
```

And in `MessageItem.jsx`, the existing `imageUrl`/`relatedImageUrl` rendering already handles single images. For multiple images from tool results, extend the rendering to handle an `images` array if present.

**Step 5: Verify & commit**

Commit: `git commit -m "feat: inline image display in streaming messages"`

---

## Task 5: Agent Tool - Image Generator

**Goal:** Create a `generate_image` tool that the LLM can invoke during the ReACT loop to generate images.

**Files:**
- Create: `backend/tools/image_tools.py`
- Modify: `backend/tools/tool_registry_init.py` (lines 412-450)
- Modify: `backend/services/unified_chat_engine.py` (tool result handling section)

**Step 1: Create `backend/tools/image_tools.py`**

```python
"""Image generation tool for the agent system."""

import logging
import os
import uuid
from datetime import datetime

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class ImageGeneratorTool(BaseTool):
    """
    Generate images from text descriptions using the local image generation pipeline.
    Use this when the user asks you to create, generate, draw, or make an image.
    """

    name = "generate_image"
    description = (
        "Generate an image from a text prompt. Returns the URL of the generated image. "
        "Use when the user asks to create, generate, draw, or visualize an image."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Detailed description of the image to generate. Be specific about subject, style, lighting, composition.",
            required=True,
        ),
        "style": ToolParameter(
            name="style",
            type="string",
            description="Image style: 'realistic', 'artistic', 'anime', 'photographic', 'digital-art'. Default: 'realistic'.",
            required=False,
            default="realistic",
        ),
        "width": ToolParameter(
            name="width",
            type="int",
            description="Image width in pixels. Default: 512. Options: 512, 768, 1024.",
            required=False,
            default=512,
        ),
        "height": ToolParameter(
            name="height",
            type="int",
            description="Image height in pixels. Default: 512. Options: 512, 768, 1024.",
            required=False,
            default=512,
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, style: str = "realistic",
                width: int = 512, height: int = 512) -> ToolResult:
        logger.info(f"ImageGeneratorTool: Generating image for prompt: {prompt[:80]}...")

        try:
            from backend.config import OUTPUT_DIR
            output_dir = os.path.join(OUTPUT_DIR, "generated_images")
            os.makedirs(output_dir, exist_ok=True)

            # Generate unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:8]
            filename = f"gen_{timestamp}_{unique_id}.png"
            output_path = os.path.join(output_dir, filename)

            # Try to use the image generation pipeline
            try:
                from backend.services.offline_image_generator import get_image_generator
                generator = get_image_generator()

                # Build generation config
                result = generator.generate_image(
                    prompt=prompt,
                    negative_prompt="blurry, low quality, distorted, deformed",
                    width=width,
                    height=height,
                    num_inference_steps=20,
                    guidance_scale=7.5,
                    output_path=output_path,
                )

                if result and os.path.exists(output_path):
                    # Create accessible URL
                    image_url = f"/api/outputs/generated_images/{filename}"
                    return ToolResult(
                        success=True,
                        output=f"Image generated successfully.\nImage URL: {image_url}\nPrompt: {prompt}\nStyle: {style}\nSize: {width}x{height}",
                        metadata={
                            "image_url": image_url,
                            "filename": filename,
                            "prompt": prompt,
                            "width": width,
                            "height": height,
                        },
                    )
                else:
                    return ToolResult(
                        success=False,
                        error="Image generation completed but output file was not created.",
                    )

            except ImportError:
                return ToolResult(
                    success=False,
                    error="Image generation pipeline not available. The Stable Diffusion model may not be installed.",
                )
            except Exception as gen_error:
                logger.error(f"Image generation error: {gen_error}", exc_info=True)
                return ToolResult(
                    success=False,
                    error=f"Image generation failed: {str(gen_error)}",
                )

        except Exception as e:
            logger.error(f"ImageGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}",
            )
```

**Step 2: Register the tool in `tool_registry_init.py`**

Add a new `register_image_tools()` function and call it from `initialize_all_tools()`:

```python
def register_image_tools() -> List[str]:
    """Register image generation tools."""
    registered = []
    try:
        from backend.tools.image_tools import ImageGeneratorTool
        register_tool(ImageGeneratorTool())
        registered.append("generate_image")
        logger.info("Registered image generation tools")
    except Exception as e:
        logger.warning(f"Failed to register image tools: {e}")
    return registered
```

In `initialize_all_tools()`, add after the existing registrations:
```python
_registered_tools.extend(register_image_tools())
```

**Step 3: Emit `chat:image` when tool result contains image URL**

In `unified_chat_engine.py`, in the tool result handling section (where tool results are emitted via Socket.IO), check if the tool result metadata contains an `image_url` and emit a `chat:image` event:

```python
# After emitting chat:tool_result, check for image:
if result.metadata and result.metadata.get("image_url"):
    emit_fn("chat:image", {
        "image_url": result.metadata["image_url"],
        "alt": f"Generated: {result.metadata.get('prompt', 'image')[:50]}",
        "caption": result.metadata.get("prompt", ""),
        "session_id": session_id,
    })
```

**Step 4: Ensure output files are servable**

Verify that `/api/outputs/` route exists to serve files from `OUTPUT_DIR`. Check `backend/routes/` or `backend/api/output_api.py` for an existing route. If not, add one:

```python
@app.route("/api/outputs/<path:filepath>")
def serve_output(filepath):
    from backend.config import OUTPUT_DIR
    return send_from_directory(OUTPUT_DIR, filepath)
```

**Step 5: Verify & commit**

Test: In chat, type "generate an image of a sunset over mountains" -> tool should be called, image generated and displayed inline.

Commit: `git commit -m "feat: generate_image agent tool with inline display"`

---

## Task 6: Multi-Image Support

**Goal:** Allow users to paste/attach multiple images before sending, with a thumbnail strip preview.

**Files:**
- Modify: `frontend/src/components/chat/ChatInput.jsx` (lines 247-252, 713-832, 1029-1102)

**Step 1: Change `imageState` to support multiple images**

```javascript
const [imageState, setImageState] = useState({
    images: [],        // Array of { file, preview, id }
    analyzing: false,
    error: null,
});
const MAX_IMAGES = 4;
```

**Step 2: Update `handleImageUpload` to accumulate**

```javascript
const handleImageUpload = (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
        setImageState(prev => ({ ...prev, error: "Please select an image file" }));
        return;
    }
    if (file.size > 20 * 1024 * 1024) {
        setImageState(prev => ({ ...prev, error: "Image too large (max 20MB)" }));
        return;
    }
    if (imageState.images.length >= MAX_IMAGES) {
        setImageState(prev => ({ ...prev, error: `Maximum ${MAX_IMAGES} images allowed` }));
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        setImageState(prev => ({
            ...prev,
            images: [...prev.images, {
                file,
                preview: e.target.result,
                id: `img_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
            }],
            error: null,
        }));
    };
    reader.readAsDataURL(file);
};
```

**Step 3: Update `clearImage` to support removing individual images**

```javascript
const clearImage = (imageId) => {
    if (imageId) {
        setImageState(prev => ({
            ...prev,
            images: prev.images.filter(img => img.id !== imageId),
        }));
    } else {
        setImageState({ images: [], analyzing: false, error: null });
    }
};
```

**Step 4: Update paste handler**

The paste handler already calls `handleImageUpload(file)` which now accumulates. No change needed.

**Step 5: Update `analyzeImage` to send multiple images**

For the unified chat path, convert all images to base64 array:
```javascript
const images = imageState.images.map(img => ({
    base64: img.preview.split(',')[1],
    name: img.file.name,
}));
onSendMessage(messageText, null, {
    isImageAnalysis: true,
    imageBase64: images[0].base64,          // Primary image for Ollama
    additionalImages: images.slice(1),       // Additional images
    imageFileName: images.map(i => i.name).join(', '),
});
```

For the legacy path, send only the first image (limitation of the `/vision/analyze` endpoint).

**Step 6: Update preview UI to show thumbnail strip**

Replace the single image preview card with a horizontal strip of thumbnails, each with a remove button:

```jsx
{imageState.images.length > 0 && (
    <Box sx={{ display: 'flex', gap: 1, p: 1, flexWrap: 'wrap' }}>
        {imageState.images.map(img => (
            <Box key={img.id} sx={{ position: 'relative', width: 80, height: 80 }}>
                <CardMedia
                    component="img"
                    sx={{ width: 80, height: 80, borderRadius: 1, objectFit: 'cover' }}
                    image={img.preview}
                    alt={img.file.name}
                />
                <IconButton
                    size="small"
                    onClick={() => clearImage(img.id)}
                    sx={{ position: 'absolute', top: -8, right: -8, bgcolor: 'background.paper' }}
                >
                    <CloseIcon fontSize="small" />
                </IconButton>
            </Box>
        ))}
        {imageState.images.length < MAX_IMAGES && (
            <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>
                +{MAX_IMAGES - imageState.images.length} more
            </Typography>
        )}
    </Box>
)}
```

**Step 7: Update `handleSend` image check**

```javascript
// Line 854-860 equivalent:
if (imageState.images.length > 0) {
    await analyzeImage();
    return;
}
```

**Step 8: Verify & commit**

Test: Paste 2-3 images, verify thumbnails show, send, verify analysis runs.
Test: Paste 5th image -> error message "Maximum 4 images allowed".

Commit: `git commit -m "feat: multi-image support with thumbnail strip preview"`

---

## Dependency Graph

```
Task 1 (Auto-Detection) ─────────────────────────────────────┐
                                                               │
Task 2 (Unified Chat Vision) ──── depends on Task 1 ─────────┤
                                                               │
Task 3 (Conversation Context) ── depends on Task 2 ──────────┤
                                                               │
Task 4 (Inline Streaming Display) ── independent ─────────────┤
                                                               │
Task 5 (Image Gen Tool) ──────── depends on Task 4 ──────────┤
                                                               │
Task 6 (Multi-Image) ───────── depends on Task 2 ─────────────┘
```

**Parallelizable:** Tasks 4+6 can run in parallel with Task 3 (no shared files).
