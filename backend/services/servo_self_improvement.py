import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import os

from backend.services.servo_knowledge_store import get_servo_archive, REFLEXES

logger = logging.getLogger(__name__)

class ServoSelfImprovement:
    """
    Automated 'Archive Miner' for self-supervised vision refinement.
    Analyzes historical click data to discover systemic biases.
    """

    def __init__(self):
        root = os.environ.get("GUAARDVARK_ROOT", ".")
        self.archive_path = Path(root) / "data" / "training" / "knowledge" / "servo_archive.jsonl"

    def analyze_biases(self, min_samples: int = 20) -> Dict[str, Any]:
        """
        Groups interactions by platform/target type and finds systemic
        coordinate errors.
        """
        if not self.archive_path.exists():
            return {"success": False, "error": "Archive not found"}

        history = []
        with open(self.archive_path) as f:
            for line in f:
                try:
                    history.append(json.loads(line))
                except:
                    continue

        if len(history) < min_samples:
            return {"success": False, "error": f"Not enough samples ({len(history)} < {min_samples})"}

        # We focus on cases where the click was issued but the screen didn't change as expected,
        # or where a subsequent correction loop moved in a consistent direction.
        
        biases = {}
        
        # Example pattern discovery: 
        # Clicks on "youtube" targets are consistently 10px too high.
        platforms = ["youtube", "reddit", "discord", "universal"]
        
        for platform in platforms:
            samples = [h for h in history if platform in h.get("target", "").lower() or platform == "universal"]
            if len(samples) < min_samples:
                continue
                
            # Calculate mean error for successful vs failed clicks
            # In a real implementation, we'd use a regression model here.
            # For v1, we look at the 'correction_log' to see which way the agent nudged.
            
            direction_counts = {"up": 0, "down": 0, "left": 0, "right": 0}
            for s in samples:
                for move in s.get("correction_log", []):
                    dir = move.get("direction")
                    if dir in direction_counts:
                        direction_counts[dir] += 1
            
            # If one direction is heavily over-represented, we have a bias.
            total_corrections = sum(direction_counts.values())
            if total_corrections > 0:
                for dir, count in direction_counts.items():
                    ratio = count / total_corrections
                    if ratio > 0.6: # Significant bias
                        biases[platform] = {"direction": dir, "strength": ratio, "samples": len(samples)}

        return {"success": True, "biases": biases}

    def suggest_reflex_updates(self) -> List[Dict[str, Any]]:
        """
        Translates discovered biases into actionable Tier 1 Reflex proposals.
        """
        analysis = self.analyze_biases()
        if not analysis.get("success"):
            return []

        proposals = []
        biases = analysis.get("biases", {})
        
        for platform, data in biases.items():
            dir = data["direction"]
            # Suggest a 5px offset adjustment in the direction of corrections
            proposals.append({
                "reflex": f"offset_{platform}_{'y' if dir in ('up', 'down') else 'x'}",
                "adjustment": 5 if dir in ("down", "right") else -5,
                "reason": f"Discovered systemic {dir} bias on {platform} ({data['strength']:.1%} confidence)",
                "platform": platform
            })
            
        return proposals

def run_self_improvement():
    miner = ServoSelfImprovement()
    proposals = miner.suggest_reflex_updates()
    if proposals:
        logger.info(f"Archive Miner found {len(proposals)} improvements:")
        for p in proposals:
            logger.info(f"  - {p['reason']}")
            # In Phase 4, we'd automatically apply these to servo_knowledge_store.py
    else:
        logger.info("Archive Miner found no significant biases. Vision is calibrated.")

if __name__ == "__main__":
    run_self_improvement()
