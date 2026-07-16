"""scripts/seed_profile.py — One-time seed for a guest profile.

Usage:
    python scripts/seed_profile.py MasiM 1401 Majesty
    python scripts/seed_profile.py <display_name> <password> [nickname]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.profile import get_registry

def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/seed_profile.py <display_name> <password> [nickname]")
        sys.exit(1)

    display_name = sys.argv[1]
    password = sys.argv[2]
    nickname = sys.argv[3] if len(sys.argv) > 3 else display_name

    reg = get_registry()
    slug = display_name.lower()

    existing = reg.get_profile(slug)
    if existing:
        print(f"Profile '{slug}' already exists (display: {existing.display_name}, nickname: {existing.greeting_name})")
        sys.exit(0)

    p = reg.create_profile(display_name, password, nickname=nickname)
    print(f"Created profile:")
    print(f"  slug:         {p.name}")
    print(f"  display_name: {p.display_name}")
    print(f"  nickname:     {p.greeting_name}")
    print(f"  db:           {p.db_path}")
    print(f"  vault:        {p.vault_path}")
    print(f"\nWania can now /login {p.name} <password> on Telegram.")

if __name__ == "__main__":
    main()
