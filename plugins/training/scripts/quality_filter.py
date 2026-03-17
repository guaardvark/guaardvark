#!/usr/bin/env python3

import json
import re
from pathlib import Path
from typing import List, Dict, Tuple
from collections import Counter


class QualityFilter:

    def __init__(self):
        self.bad_patterns = [
            r"i don't have access",
            r"as an ai",
            r"i cannot browse",
            r"i'm not able to",
            r"my training data",
            r"i was trained",
            r"knowledge cutoff",
            r"i apologize, but i",
            r"i'm just an ai",
        ]

        self.hallucination_markers = [
            r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}.*lottery",
            r"winning numbers.*\d+.*\d+.*\d+",
            r"current temperature.*\d+.*degrees",
            r"stock price.*\$\d+",
            r"score.*\d+-\d+",
        ]

        self.stats = Counter()

    def score_example(self, instruction: str, output: str) -> Tuple[float, List[str]]:
        issues = []
        score = 1.0

        if len(instruction) < 10:
            issues.append("instruction_too_short")
            score -= 0.3

        if len(output) < 20:
            issues.append("output_too_short")
            score -= 0.2

        if len(output) > 5000:
            issues.append("output_too_long")
            score -= 0.1

        output_lower = output.lower()
        for pattern in self.bad_patterns:
            if re.search(pattern, output_lower):
                issues.append(f"ai_limitation_phrase")
                score -= 0.2
                break

        for pattern in self.hallucination_markers:
            if re.search(pattern, output_lower, re.IGNORECASE):
                issues.append("potential_hallucination")
                score -= 0.5
                break

        if output_lower.startswith(
            ("sure!", "certainly!", "of course!", "absolutely!")
        ):
            issues.append("sycophantic_opener")
            score -= 0.1

        if instruction.lower() in output_lower[:100]:
            issues.append("echo_instruction")
            score -= 0.1

        return max(0, score), issues

    def filter_dataset(
        self,
        input_path: str,
        output_path: str = None,
        min_score: float = 0.5,
        verbose: bool = True,
    ) -> Tuple[str, Dict]:
        input_path = Path(input_path)
        output_path = (
            Path(output_path)
            if output_path
            else input_path.with_suffix(".filtered.jsonl")
        )

        kept = []
        removed = []

        with open(input_path) as f:
            for line_num, line in enumerate(f, 1):
                try:
                    example = json.loads(line)
                    instruction = example.get("instruction", "")
                    output = example.get("output", "")

                    score, issues = self.score_example(instruction, output)

                    for issue in issues:
                        self.stats[issue] += 1

                    if score >= min_score:
                        example["_quality_score"] = score
                        kept.append(example)
                    else:
                        removed.append(
                            {
                                "line": line_num,
                                "score": score,
                                "issues": issues,
                                "instruction_preview": instruction[:100],
                            }
                        )

                except json.JSONDecodeError:
                    self.stats["json_error"] += 1

        with open(output_path, "w") as f:
            for example in kept:
                f.write(json.dumps(example) + "\n")

        report = {
            "input": str(input_path),
            "output": str(output_path),
            "total": len(kept) + len(removed),
            "kept": len(kept),
            "removed": len(removed),
            "issues": dict(self.stats),
            "removed_examples": removed[:20],
        }

        if verbose:
            print(f"\n=== Quality Filter Report ===")
            print(f"Input: {input_path}")
            print(f"Total examples: {report['total']}")
            print(
                f"Kept: {report['kept']} ({100*report['kept']/max(1,report['total']):.1f}%)"
            )
            print(f"Removed: {report['removed']}")
            print(f"\nIssue breakdown:")
            for issue, count in sorted(self.stats.items(), key=lambda x: -x[1]):
                print(f"  {issue}: {count}")
            print(f"\nFiltered output: {output_path}")

        return str(output_path), report

    def interactive_review(self, input_path: str, output_path: str = None):
        input_path = Path(input_path)
        output_path = (
            Path(output_path)
            if output_path
            else input_path.with_suffix(".reviewed.jsonl")
        )

        approved = []
        rejected = []

        with open(input_path) as f:
            examples = [json.loads(line) for line in f]

        print(f"\n=== Interactive Review ===")
        print(f"Total examples: {len(examples)}")
        print("Commands: [y]es, [n]o, [e]dit, [s]kip, [q]uit\n")

        for i, example in enumerate(examples):
            instruction = example.get("instruction", "")
            output = example.get("output", "")
            score, issues = self.score_example(instruction, output)

            print(f"\n--- Example {i+1}/{len(examples)} (score: {score:.2f}) ---")
            if issues:
                print(f"Issues: {', '.join(issues)}")
            print(f"\nINSTRUCTION:\n{instruction[:500]}")
            print(f"\nOUTPUT:\n{output[:500]}{'...' if len(output) > 500 else ''}")

            while True:
                choice = input("\n[y/n/e/s/q]: ").lower().strip()

                if choice == "y":
                    approved.append(example)
                    break
                elif choice == "n":
                    rejected.append(example)
                    break
                elif choice == "s":
                    break
                elif choice == "q":
                    with open(output_path, "w") as f:
                        for ex in approved:
                            f.write(json.dumps(ex) + "\n")
                    print(f"\nSaved {len(approved)} approved examples to {output_path}")
                    return str(output_path)
                elif choice == "e":
                    print("Edit mode not implemented yet")
                else:
                    print("Invalid choice")

        with open(output_path, "w") as f:
            for ex in approved:
                f.write(json.dumps(ex) + "\n")

        print(f"\n=== Review Complete ===")
        print(f"Approved: {len(approved)}")
        print(f"Rejected: {len(rejected)}")
        print(f"Output: {output_path}")

        return str(output_path)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Guaardvark Quality Filter")
    parser.add_argument("input", help="Input JSONL file")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument(
        "--min-score", type=float, default=0.5, help="Minimum quality score (0-1)"
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Interactive review mode"
    )

    args = parser.parse_args()

    qf = QualityFilter()

    if args.interactive:
        qf.interactive_review(args.input, args.output)
    else:
        qf.filter_dataset(args.input, args.output, min_score=args.min_score)


if __name__ == "__main__":
    main()
