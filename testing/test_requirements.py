"""
Requirements Verification Test
Ensures all dependencies are installed and importable
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_all_imports():
    """Test that all required packages can be imported"""
    checks = [
        ("anthropic", "anthropic"),
        ("groq", "groq"),
        ("google.genai", "google.genai"),
        ("supabase", "supabase"),
        ("dotenv", "python-dotenv"),
    ]

    missing = []
    for import_name, package_name in checks:
        try:
            __import__(import_name)
            print(f"  ✓ {package_name}")
        except ImportError as e:
            print(f"  ✗ {package_name} - NOT INSTALLED: {e}")
            missing.append(package_name)

    # ollama optional
    try:
        import ollama
        print(f"  ✓ ollama (optional)")
    except ImportError:
        print(f"  ⚠ ollama - not installed (optional, for local model support)")

    if missing:
        print(f"\n  Missing required packages: {', '.join(missing)}")
        print(f"  Install with: pip install {' '.join(missing)}")
        return False

    print(f"\n  ✓ All required packages installed")
    return True


def test_env_vars():
    """Test that required environment variables are set"""
    from dotenv import load_dotenv
    load_dotenv()

    required_vars = [
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_KEY",
    ]

    optional_vars = [
        "GEMINI_API_KEY",
    ]

    missing = []
    for var in required_vars:
        val = os.getenv(var)
        if val:
            print(f"  ✓ {var} = {val[:8]}...")
        else:
            print(f"  ✗ {var} - NOT SET")
            missing.append(var)

    for var in optional_vars:
        val = os.getenv(var)
        if val:
            print(f"  ✓ {var} = {val[:8]}... (optional)")
        else:
            print(f"  ⚠ {var} - not set (optional)")

    if missing:
        print(f"\n  Missing required env vars: {', '.join(missing)}")
        return False

    print(f"\n  ✓ All required env vars set")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("REQUIREMENTS VERIFICATION")
    print("=" * 60)

    print("\n--- Package Imports ---")
    imports_ok = test_all_imports()

    print("\n--- Environment Variables ---")
    env_ok = test_env_vars()

    print("\n" + "=" * 60)
    if imports_ok and env_ok:
        print("✓ ALL REQUIREMENTS SATISFIED")
    else:
        print("✗ REQUIREMENTS NOT MET - Fix above issues before running Pi")
    print("=" * 60)

    sys.exit(0 if (imports_ok and env_ok) else 1)
