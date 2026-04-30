"""ARIA assistant entry point."""

from core.agent import run_agent


def main() -> None:
    user_input = input("You: ")
    run_agent(user_input)


if __name__ == "__main__":
    main()
