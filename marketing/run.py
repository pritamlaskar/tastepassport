"""
CLI entry point for the TastePassport Marketing Agent.

Usage:
  python run.py daily                          # Generate content for top 3 cities
  python run.py city bangkok                   # Generate content for a specific city
  python run.py reply twitter "post" "comment" # Generate a reply to a comment
  python run.py report                         # Show performance summary
  python run.py schedule                       # Start the scheduler daemon

Environment (from .env or environment):
  ANTHROPIC_API_KEY      required
  TP_API_URL             default: http://localhost:8000
  TARGET_CITIES          comma-separated city slugs for daily run (optional)
  TWITTER_BEARER_TOKEN   optional — for direct posting
  INSTAGRAM_ACCESS_TOKEN optional — for direct posting
  LINKEDIN_ACCESS_TOKEN  optional — for direct posting
"""
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(f"Error: {key} is not set. Add it to your .env file.")
        sys.exit(1)
    return val


def get_agent():
    from agent import TastePassportMarketingAgent
    return TastePassportMarketingAgent(
        anthropic_api_key=require_env("ANTHROPIC_API_KEY"),
        api_base_url=os.getenv("TP_API_URL", "http://localhost:8000"),
        twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN"),
        linkedin_access_token=os.getenv("LINKEDIN_ACCESS_TOKEN"),
    )


def cmd_daily():
    cities_env = os.getenv("TARGET_CITIES", "")
    cities = [c.strip() for c in cities_env.split(",")] if cities_env else None
    agent = get_agent()
    agent.run_daily(cities=cities)


def cmd_city(city: str):
    agent = get_agent()
    content = agent.run_for_city(city)
    print(f"\nGenerated {sum(len(v) for v in [content] if isinstance(v, list))} pieces for {city}")


def cmd_reply(platform: str, post: str, comment: str):
    agent = get_agent()
    reply = agent.generate_reply(platform, post, comment)
    print(f"\n--- Reply for {platform} ---\n{reply}")


def cmd_report():
    agent = get_agent()
    report = agent.performance_report()
    print("\n=== TastePassport Marketing Agent — Performance Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")


def cmd_schedule():
    import scheduler as sched_module
    sched_module.main()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "daily":
        cmd_daily()

    elif cmd == "city":
        if len(args) < 2:
            print("Usage: python run.py city <city-slug>")
            sys.exit(1)
        cmd_city(args[1])

    elif cmd == "reply":
        if len(args) < 4:
            print('Usage: python run.py reply <platform> "<post summary>" "<comment>"')
            sys.exit(1)
        cmd_reply(args[1], args[2], args[3])

    elif cmd == "report":
        cmd_report()

    elif cmd == "schedule":
        cmd_schedule()

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: daily, city, reply, report, schedule")
        sys.exit(1)


if __name__ == "__main__":
    main()
