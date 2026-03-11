#!/usr/bin/env python3

import json
import re
import os
from pathlib import Path
from typing import List, Dict, Generator
from datetime import datetime

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


class TranscriptParser:

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir or os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'training', 'processed'))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"total_files": 0, "total_pairs": 0, "errors": 0}

    def parse_file(self, filepath: str) -> List[Dict]:
        filepath = Path(filepath)

        if not filepath.exists():
            print(f"File not found: {filepath}")
            return []

        if filepath.suffix == '.docx':
            return self._parse_docx(filepath)

        content = filepath.read_text(encoding='utf-8', errors='ignore')

        if filepath.suffix == '.jsonl':
            return self._parse_jsonl(content)
        elif filepath.suffix == '.json':
            return self._parse_json(content)
        elif filepath.suffix in ['.md', '.txt', '.html']:
            return self._parse_text(content)
        else:
            return self._parse_text(content)

    def _parse_docx(self, filepath: Path) -> List[Dict]:
        if not DOCX_AVAILABLE:
            print(f"python-docx not installed, skipping {filepath.name}")
            return []

        try:
            doc = DocxDocument(str(filepath))
            text = '\n'.join([para.text for para in doc.paragraphs])
            return self._parse_text(text)
        except Exception as e:
            print(f"Error parsing {filepath.name}: {e}")
            return []

    def _parse_jsonl(self, content: str) -> List[Dict]:
        pairs = []
        messages = []

        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError:
                continue

        pairs.extend(self._extract_pairs_from_messages(messages))
        return pairs

    def _parse_json(self, content: str) -> List[Dict]:
        pairs = []
        try:
            data = json.loads(content)

            if isinstance(data, list):
                if all('role' in m for m in data if isinstance(m, dict)):
                    pairs.extend(self._extract_pairs_from_messages(data))
                elif all('mapping' in c for c in data if isinstance(c, dict)):
                    for conv in data:
                        pairs.extend(self._parse_chatgpt_export(conv))
            elif isinstance(data, dict):
                if 'messages' in data:
                    pairs.extend(self._extract_pairs_from_messages(data['messages']))
                elif 'mapping' in data:
                    pairs.extend(self._parse_chatgpt_export(data))

        except json.JSONDecodeError:
            pass
        return pairs

    def _parse_chatgpt_export(self, conv: Dict) -> List[Dict]:
        pairs = []
        mapping = conv.get('mapping', {})

        messages = []
        for node in mapping.values():
            msg = node.get('message')
            if msg and msg.get('content', {}).get('parts'):
                role = msg.get('author', {}).get('role', 'unknown')
                content = ' '.join(msg['content']['parts'])
                if content.strip():
                    messages.append({'role': role, 'content': content})

        pairs.extend(self._extract_pairs_from_messages(messages))
        return pairs

    def _parse_text(self, content: str) -> List[Dict]:
        pairs = []

        patterns = [
            (r'(?:Human|User|Me|You|Q):\s*(.+?)(?=(?:Assistant|AI|Claude|GPT|Gemini|ChatGPT|A|Response):|$)',
             r'(?:Assistant|AI|Claude|GPT|Gemini|ChatGPT|A|Response):\s*(.+?)(?=(?:Human|User|Me|You|Q):|$)'),
            (r'\*\*(?:Human|User|Me|You)\*\*[:\s]*(.+?)(?=\*\*(?:Assistant|AI|Claude|GPT|Gemini|ChatGPT)\*\*|$)',
             r'\*\*(?:Assistant|AI|Claude|GPT|Gemini|ChatGPT)\*\*[:\s]*(.+?)(?=\*\*(?:Human|User|Me|You)\*\*|$)'),
            (r'You said:\s*(.+?)(?=ChatGPT said:|$)',
             r'ChatGPT said:\s*(.+?)(?=You said:|$)'),
            (r'\[You\]\s*(.+?)(?=\[(?:Assistant|Claude|GPT|AI)\]|$)',
             r'\[(?:Assistant|Claude|GPT|AI)\]\s*(.+?)(?=\[You\]|$)'),
        ]

        for human_pattern, ai_pattern in patterns:
            human_matches = re.findall(human_pattern, content, re.DOTALL | re.IGNORECASE)
            ai_matches = re.findall(ai_pattern, content, re.DOTALL | re.IGNORECASE)

            if human_matches and ai_matches:
                for h, a in zip(human_matches, ai_matches):
                    h_clean = self._clean_text(h)
                    a_clean = self._clean_text(a)
                    if h_clean and a_clean and len(h_clean) > 5 and len(a_clean) > 10:
                        pairs.append({
                            "instruction": h_clean,
                            "output": a_clean
                        })
                if pairs:
                    break

        return pairs

    def _clean_text(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'^\s*[-•]\s*', '', text)
        return text

    def _extract_pairs_from_messages(self, messages: List[Dict]) -> List[Dict]:
        pairs = []

        user_msg = None
        for msg in messages:
            role = msg.get('role', msg.get('type', '')).lower()
            content = msg.get('content', '')

            if isinstance(content, list):
                content = ' '.join(str(c.get('text', c)) for c in content if isinstance(c, dict))

            content = str(content).strip()

            if role in ['user', 'human']:
                user_msg = content
            elif role in ['assistant', 'ai', 'model'] and user_msg:
                if len(user_msg) > 5 and len(content) > 10:
                    pairs.append({
                        "instruction": user_msg,
                        "output": content
                    })
                user_msg = None

        return pairs

    def process_directory(self, input_dir: str, recursive: bool = True) -> str:
        input_path = Path(input_dir)
        all_pairs = []

        pattern = '**/*' if recursive else '*'

        for filepath in input_path.glob(pattern):
            if filepath.is_file() and filepath.suffix in ['.json', '.jsonl', '.txt', '.md', '.docx', '.html']:
                print(f"Processing: {filepath.name}")
                self.stats["total_files"] += 1
                try:
                    pairs = self.parse_file(str(filepath))
                    all_pairs.extend(pairs)
                    self.stats["total_pairs"] += len(pairs)
                except Exception as e:
                    print(f"  Error: {e}")
                    self.stats["errors"] += 1

        output_file = self.output_dir / f"training_corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

        with open(output_file, 'w') as f:
            for pair in all_pairs:
                f.write(json.dumps(pair) + '\n')

        print(f"\n=== Processing Complete ===")
        print(f"Files processed: {self.stats['total_files']}")
        print(f"Training pairs extracted: {self.stats['total_pairs']}")
        print(f"Errors: {self.stats['errors']}")
        print(f"Output: {output_file}")

        return str(output_file)

    def create_alpaca_format(self, input_jsonl: str, output_file: str = None) -> str:
        input_path = Path(input_jsonl)
        output_path = Path(output_file) if output_file else input_path.with_suffix('.alpaca.json')

        alpaca_data = []

        with open(input_path) as f:
            for line in f:
                pair = json.loads(line)
                alpaca_data.append({
                    "instruction": pair.get("instruction", ""),
                    "input": "",
                    "output": pair.get("output", "")
                })

        with open(output_path, 'w') as f:
            json.dump(alpaca_data, f, indent=2)

        print(f"Alpaca format saved: {output_path}")
        return str(output_path)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Guaardvark Training Corpus Builder')
    parser.add_argument('input', help='Input file or directory')
    parser.add_argument('-o', '--output', help='Output directory', default=None)
    parser.add_argument('-r', '--recursive', action='store_true', help='Process directories recursively')
    parser.add_argument('--alpaca', action='store_true', help='Also create Alpaca format')

    args = parser.parse_args()

    tp = TranscriptParser(output_dir=args.output)

    input_path = Path(args.input)

    if input_path.is_dir():
        output_file = tp.process_directory(args.input, recursive=args.recursive)
    else:
        pairs = tp.parse_file(args.input)
        output_file = tp.output_dir / f"training_{input_path.stem}.jsonl"
        with open(output_file, 'w') as f:
            for pair in pairs:
                f.write(json.dumps(pair) + '\n')
        print(f"Extracted {len(pairs)} pairs -> {output_file}")

    if args.alpaca and output_file:
        tp.create_alpaca_format(output_file)


if __name__ == "__main__":
    main()
