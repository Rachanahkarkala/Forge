
import asyncio
from pathlib import Path

from app.agents.impact_analyzer import analyze_impact
from app.agents.investigator import investigate_issue


async def main():
    repo = Path("../workspaces/fastapi-fastapi-b1d4d6e6")

    issue = (
        "Add tests for path endpoints with numeric constraints "
        "gt, lt, ge and le."
    )

    print("Running Forge impact analysis...")

    impact = analyze_impact(repo, issue)

    print("Sending evidence to investigation agent...")

    report = await investigate_issue(impact)

    print("\n========== FORGE INVESTIGATION ==========")

    for key, value in report.items():
        print(f"\n{key.upper()}:")
        print(value)


if __name__ == "__main__":
    asyncio.run(main())
