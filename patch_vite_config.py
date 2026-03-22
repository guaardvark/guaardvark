import re

with open('frontend/vite.config.js', 'r') as f:
    content = f.read()

# Add resolve alias
alias_insertion = """  resolve: {
    alias: {
      'onnxruntime-web/wasm': 'onnxruntime-web/dist/ort.wasm.min.mjs',
    },
  },
"""
if 'resolve: {' not in content:
    content = content.replace('export default defineConfig({', 'export default defineConfig({\n' + alias_insertion)

# Update optimizeDeps: include both vad-web and onnxruntime-web
content = re.sub(r'optimizeDeps: \{.*?exclude: \[.*?\]', 
                 'optimizeDeps: {\n    include: [\n      "@ricky0123/vad-web",\n      "onnxruntime-web"', 
                 content, flags=re.MULTILINE | re.DOTALL)

with open('frontend/vite.config.js', 'w') as f:
    f.write(content)
