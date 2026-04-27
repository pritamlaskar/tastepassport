"""
Scheduler — runs the TastePassport marketing agent on a daily cadence.

Posting schedule (all times local):
  08:00  Twitter city thread (best engagement window)
  10:00  Instagram city guide
  12:00  Twitter restaurant spotlight
  17:00  LinkedIn data insight / founder post
  19:00  Twitter hot take
  20:00  Instagram restaurant spotlight

Run: python scheduler.py
Stop: Ctrl+C
"""
import logging
import os
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("marketing_agent.log"),
    ],
)
logger = logging.getLogger(__name__)


def get_agent():
    from agent import TastePassportMarketingAgent
    return TastePassportMarketingAgent(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        api_base_url=os.getenv("TP_API_URL", "http://localhost:8000"),
        twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN"),
        linkedin_access_token=os.getenv("LINKEDIN_ACCESS_TOKEN"),
    )


def daily_content_run():
    """Full daily content generation run."""
    logger.info(f"Starting daily content run at {datetime.now().isoformat()}")
    try:
        agent = get_agent()
        cities_env = os.getenv("TARGET_CITIES", "")
        cities = [c.strip() for c in cities_env.split(",")] if cities_env else None
        agent.run_daily(cities=cities)
        logger.info("Daily run complete.")
    except Exception as e:
        logger.error(f"Daily run failed: {e}", exc_info=True)


def performance_check():
    """Weekly performance report — log what's been generated."""
    try:
        agent = get_agent()
        report = agent.performance_report()
        logger.info(f"Performance report: {report}")
    except Exception as e:
        logger.error(f"Performance check failed: {e}", exc_info=True)


def main():
    scheduler = BlockingScheduler(timezone="UTC")

    # Daily content generation at 07:00 UTC (content ready before peak hours)
    scheduler.add_job(
        daily_content_run,
        CronTrigger(hour=7, minute=0),
        id="daily_content",
        name="Daily content generation",
        replace_existing=True,
    )

    # Weekly performance summary every Monday at 06:00 UTC
    scheduler.add_job(
        performance_check,
        CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="weekly_performance",
        name="Weekly performance report",
        replace_existing=True,
    )

    logger.info("Marketing agent scheduler started.")
    logger.info("Daily content generation: 07:00 UTC")
    logger.info("Weekly performance report: Monday 06:00 UTC")
    logger.info("Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
