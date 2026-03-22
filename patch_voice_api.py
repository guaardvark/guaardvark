import re

with open('backend/api/voice_api.py', 'r') as f:
    content = f.read()

# Add import for faster-whisper at the top if not exists
if "from backend.utils.faster_whisper_utils import transcribe_audio_faster" not in content:
    content = content.replace("from flask import Blueprint, request, jsonify, Response, stream_with_context", 
                              "from flask import Blueprint, request, jsonify, Response, stream_with_context\nfrom backend.utils.faster_whisper_utils import transcribe_audio_faster")

# Replace whisper.cpp call with faster-whisper call
# The block to replace starts from `cmd = [` until `if not final_text:`

whisper_exec_block_regex = r"cmd = \[.*?result\.returncode != 0:\n\s+logger\.error.*?return jsonify\(\{\"error\": f\"Speech recognition failed: \{result\.stderr\}\"\}\), 500\n\s+# ENHANCED: Parse Whisper output using enhanced parsing function\n\s+final_text = parse_whisper_output\(result\.stdout\)"

new_exec_block = """
            # PERFORMANCE OPTIMIZATION: Use faster-whisper for transcription
            logger.info(f"Running faster-whisper with model: {selected_model['name']}")
            start_time = time.time()
            
            try:
                final_text, _ = transcribe_audio_faster(audio_file_for_whisper, model_size=selected_model['name'].replace('.bin', '').replace('ggml-', ''))
                processing_time = time.time() - start_time
                logger.info(f"Voice API: Processing completed in {processing_time:.2f} seconds")
            except Exception as e:
                logger.error(f"faster-whisper failed: {e}")
                return jsonify({"error": f"Speech recognition failed: {e}"}), 500
"""

content = re.sub(whisper_exec_block_regex, new_exec_block, content, flags=re.MULTILINE | re.DOTALL)

with open('backend/api/voice_api.py', 'w') as f:
    f.write(content)

