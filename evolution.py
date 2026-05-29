"""
Pi Self-Evolution System
Pi analyzes performance, modifies itself, learns patterns
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from collections import defaultdict


class EvolutionTracker:
    """
    Tracks Pi's performance and enables self-improvement.
    """
    
    def __init__(self, log_path: str = None):
        if log_path is None:
            project_root = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(project_root, "logs", "evolution.jsonl")
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    def log_interaction(
        self,
        user_input: str,
        pi_response: str,
        tool_calls: List[Dict],
        success: bool,
        mode: str,
        cost: float = 0.0,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        metadata: Optional[Dict] = None
    ):
        """Log an interaction for later analysis"""

        md = metadata or {}
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": md.get("session_id", ""),
            "mode": mode,
            "model": model,
            "success": success,
            "cost": round(cost, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tools_used": [tc.get("name", "") for tc in tool_calls],
            "tool_calls": tool_calls,
            "user_message_length": len(user_input),
            "response_length": len(pi_response),
            "metadata": md
        }

        with open(self.log_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def analyze_performance(self, days: int = 7) -> Dict:
        """
        Analyze performance over last N days.
        
        Returns patterns, failures, successes.
        """
        
        if not os.path.exists(self.log_path):
            return {"error": "No interaction logs found"}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # T-110: tail-stream the recent portion (O(max_bytes) not O(file)).
        # At ~200B/entry and 50 entries/day, 7 days = ~70KB — well inside 5MB.
        # If a full-history scan is needed (days > 90), accept the full read.
        _TAIL_BYTES = 5_000_000
        log_path = self.log_path
        interactions = []
        try:
            file_size = os.path.getsize(log_path)
            read_size = min(file_size, _TAIL_BYTES)
            with open(log_path, "rb") as f:
                f.seek(max(0, file_size - read_size))
                raw = f.read()
            lines = raw.split(b"\n")
            if read_size < file_size:
                lines = lines[1:]  # drop partial first line
            for line_bytes in lines:
                stripped = line_bytes.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                try:
                    ts_str = entry["timestamp"]
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts > cutoff:
                        interactions.append(entry)
                except (KeyError, ValueError):
                    continue
        except Exception:
            return {"error": "Failed to read interaction logs"}
        
        if not interactions:
            return {"error": "No interactions in timeframe"}
        
        # Analyze
        total = len(interactions)
        successful = sum(1 for i in interactions if i["success"])
        failed = total - successful
        
        # Tool usage — prefer the structured "tool_calls" field; fall back to
        # the legacy "tools_used" name list for entries written before SM-001 fix.
        tool_usage = defaultdict(int)
        tool_success = defaultdict(lambda: {"total": 0, "success": 0})

        for interaction in interactions:
            tool_call_list = interaction.get("tool_calls", [])
            if not tool_call_list and interaction.get("tools_used"):
                tool_call_list = [{"name": name} for name in interaction["tools_used"]]

            for tool_call in tool_call_list:
                tool_name = tool_call.get("name", "unknown")
                tool_usage[tool_name] += 1
                tool_success[tool_name]["total"] += 1
                if interaction["success"]:
                    tool_success[tool_name]["success"] += 1

        # Mode usage
        mode_usage = defaultdict(int)
        for interaction in interactions:
            mode_usage[interaction["mode"]] += 1

        # Failed model breakdown
        failed_interactions = [i for i in interactions if not i["success"]]
        failed_by_model = defaultdict(int)
        for interaction in failed_interactions:
            failed_by_model[interaction.get("model", "unknown")] += 1

        # Per-session breakdown — top-level session_id first, metadata.session_id fallback
        sessions = defaultdict(lambda: {"interactions": 0, "successful": 0, "failed": 0, "cost": 0.0})
        for interaction in interactions:
            sid = (interaction.get("session_id")
                   or interaction.get("metadata", {}).get("session_id")
                   or "unknown")
            sessions[sid]["interactions"] += 1
            if interaction["success"]:
                sessions[sid]["successful"] += 1
            else:
                sessions[sid]["failed"] += 1
            sessions[sid]["cost"] = round(sessions[sid]["cost"] + interaction.get("cost", 0.0), 6)

        return {
            "timeframe_days": days,
            "total_interactions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total if total > 0 else 0,
            "tool_usage": dict(tool_usage),
            "tool_success_rates": {
                tool: s["success"] / s["total"]
                for tool, s in tool_success.items()
            },
            "mode_usage": dict(mode_usage),
            "failed_by_model": dict(failed_by_model),
            "sessions": dict(sessions)
        }
    
    def identify_improvements(self, analysis: Dict) -> List[Dict]:
        """
        Based on analysis, identify what to improve.
        
        Returns list of improvement suggestions.
        """
        
        improvements = []
        
        # Low success rate
        if analysis.get("success_rate", 1.0) < 0.8:
            improvements.append({
                "type": "success_rate",
                "severity": "high",
                "issue": f"Success rate only {analysis['success_rate']:.1%}",
                "suggestion": "Review failed interactions and update consciousness prompt with better decision logic"
            })
        
        # Tool-specific issues
        for tool, rate in analysis.get("tool_success_rates", {}).items():
            if rate < 0.7 and analysis["tool_usage"].get(tool, 0) > 5:
                improvements.append({
                    "type": "tool_failure",
                    "severity": "medium",
                    "issue": f"Tool '{tool}' failing {(1-rate):.1%} of the time",
                    "suggestion": f"Review {tool} implementation or update consciousness guidance on when to use it"
                })
        
        # Mode imbalance
        mode_usage = analysis.get("mode_usage", {})
        if mode_usage.get("root", 0) > mode_usage.get("normie", 0) * 2:
            improvements.append({
                "type": "cost_concern",
                "severity": "low",
                "issue": "Root mode (paid) used more than normie (free)",
                "suggestion": "Review consciousness prompt to prefer free tier unless quality critical"
            })
        
        return improvements
    
    def propose_consciousness_update(self, improvements: List[Dict]) -> Optional[str]:
        """
        Generate consciousness prompt modification based on improvements.
        
        Returns proposed new consciousness section.
        """
        
        if not improvements:
            return None
        
        high_severity = [i for i in improvements if i["severity"] == "high"]
        
        if not high_severity:
            return None
        
        # Build proposed update
        update = "## PERFORMANCE IMPROVEMENTS\n\n"
        update += f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
        
        for improvement in high_severity:
            update += f"### Issue: {improvement['issue']}\n"
            update += f"Action: {improvement['suggestion']}\n\n"
        
        return update
    
    def track_pattern(self, pattern_name: str, success: bool, metadata: Optional[Dict] = None):
        """
        Track specific pattern performance.
        
        For learning what works.
        """
        
        pattern_log = os.path.join(os.path.dirname(self.log_path), "patterns.jsonl")

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pattern": pattern_name,
            "success": success,
            "metadata": metadata or {}
        }

        with open(pattern_log, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def get_pattern_success_rate(self, pattern_name: str, days: int = 30) -> float:
        """Get success rate for specific pattern"""
        
        pattern_log = os.path.join(os.path.dirname(self.log_path), "patterns.jsonl")

        if not os.path.exists(pattern_log):
            return 0.0
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        total = 0
        successful = 0
        
        with open(pattern_log, 'r') as f:
            for line in f:
                entry = json.loads(line.strip())
                
                if entry["pattern"] != pattern_name:
                    continue
                
                if datetime.fromisoformat(entry["timestamp"]) < cutoff:
                    continue
                
                total += 1
                if entry["success"]:
                    successful += 1
        
        return successful / total if total > 0 else 0.0

    def get_daily_cost(self) -> float:
        """Return total API cost spent in the last 24 hours"""
        recent = self.get_recent_interactions(hours=24)
        return round(sum(i.get("cost", 0.0) for i in recent), 6)

    def get_recent_interactions(self, hours: int = 24) -> List[Dict]:
        """Get interactions from the last N hours"""
        if not os.path.exists(self.log_path):
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent = []

        with open(self.log_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if datetime.fromisoformat(entry["timestamp"]) > cutoff:
                        recent.append(entry)
                except Exception:
                    continue

        return recent


# T-088 (R7): SelfModifier class removed — Phase 5 cruft, zero callers,
# loaded-gun risk. Archived to docs/_archive/evolution_self_modifier_v1.py
# with full rationale. Autonomous self-improvement now lives in
# scripts/sprint.py with LLM-edit + diff-review + verify gates.


if __name__ == "__main__":
    # Test
    tracker = EvolutionTracker()
    
    # Log some test interactions
    tracker.log_interaction(
        user_input="test query",
        pi_response="test response",
        tool_calls=[{"name": "memory_read", "input": {"query": "test"}}],
        success=True,
        mode="normie"
    )
    
    # Analyze
    analysis = tracker.analyze_performance(days=7)
    print(json.dumps(analysis, indent=2))
    
    # Identify improvements
    improvements = tracker.identify_improvements(analysis)
    for imp in improvements:
        print(f"\n{imp['severity'].upper()}: {imp['issue']}")
        print(f"Suggestion: {imp['suggestion']}")