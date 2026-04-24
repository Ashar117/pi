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

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "model": model,
            "success": success,
            "cost": round(cost, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tools_used": [tc.get("name", "") for tc in tool_calls],
            "user_message_length": len(user_input),
            "response_length": len(pi_response),
            "metadata": metadata or {}
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
        
        # Load logs
        interactions = []
        with open(self.log_path, 'r') as f:
            for line in f:
                entry = json.loads(line.strip())
                if datetime.fromisoformat(entry["timestamp"]) > cutoff:
                    interactions.append(entry)
        
        if not interactions:
            return {"error": "No interactions in timeframe"}
        
        # Analyze
        total = len(interactions)
        successful = sum(1 for i in interactions if i["success"])
        failed = total - successful
        
        # Tool usage
        tool_usage = defaultdict(int)
        tool_success = defaultdict(lambda: {"total": 0, "success": 0})
        
        for interaction in interactions:
            for tool_call in interaction.get("tool_calls", []):
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
            "failed_by_model": dict(failed_by_model)
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


class SelfModifier:
    """
    Allows Pi to modify its own code/prompts.
    """
    
    def __init__(self, project_root: str = None):
        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(__file__))
        self.project_root = project_root

    def modify_consciousness(self, section: str, new_content: str, backup: bool = True) -> Dict:
        """
        Modify consciousness prompt.
        
        Args:
            section: Which section to modify (must be unique)
            new_content: New content for that section
            backup: Create backup first
        
        Returns:
            {"success": bool, "backup_path": str}
        """
        
        consciousness_path = os.path.join(self.project_root, "prompts", "consciousness.txt")
        
        if not os.path.exists(consciousness_path):
            return {"success": False, "error": "Consciousness file not found"}
        
        # Backup
        if backup:
            backup_path = consciousness_path + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(consciousness_path, 'r') as f:
                content = f.read()
            with open(backup_path, 'w') as f:
                f.write(content)
        else:
            backup_path = None
        
        # Modify
        try:
            with open(consciousness_path, 'r') as f:
                content = f.read()
            
            if section not in content:
                return {
                    "success": False,
                    "error": f"Section '{section}' not found in consciousness"
                }
            
            # Replace section
            new_content_full = content.replace(section, new_content)
            
            with open(consciousness_path, 'w') as f:
                f.write(new_content_full)
            
            return {
                "success": True,
                "backup_path": backup_path,
                "message": "Consciousness updated"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def add_tool(self, tool_name: str, tool_code: str) -> Dict:
        """
        Add new tool to Pi's capabilities.
        
        Args:
            tool_name: Name of new tool
            tool_code: Python code for tool
        
        Returns:
            {"success": bool}
        """
        
        tools_path = os.path.join(self.project_root, "tools", f"tool_{tool_name}.py")
        
        try:
            with open(tools_path, 'w') as f:
                f.write(tool_code)
            
            return {
                "success": True,
                "path": tools_path,
                "message": f"Tool '{tool_name}' created"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


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