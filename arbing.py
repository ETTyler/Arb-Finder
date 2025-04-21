import requests
import time
import math
import os
from dotenv import load_dotenv
from itertools import chain
from typing import Generator, Iterable
from colorama import Fore, Style

load_dotenv()

API_KEY = os.getenv("API_KEY")
REGION = "uk"
MARKET = "h2h"
CURRENCY = "GBP"
TOTAL_STAKE = 1000
CUTOFF = 0.01  # Arbitrage margin threshold


class APIException(RuntimeError):
    def __str__(self):
        return f"('{self.args[0]}', '{self.args[1].json()['message']}')"


class AuthenticationException(APIException):
    pass


class RateLimitException(APIException):
    pass


def handle_faulty_response(response: requests.Response):
    if response.status_code == 401:
        raise AuthenticationException("Failed to authenticate with the API. Is the API key valid?", response)
    elif response.status_code == 429:
        raise RateLimitException("Encountered API rate limit.", response)
    else:
        raise APIException("Unknown issue arose while trying to access the API.", response)


def get_sports(key: str) -> set[str]:
    url = f"https://api.the-odds-api.com/v4/sports/"
    response = requests.get(url, params={"apiKey": key})
    if not response:
        handle_faulty_response(response)
    return {item["key"] for item in response.json() if not item.get("has_outrights", False)}


def get_data(key: str, sport: str, region: str = "uk"):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
    params = {
        "apiKey": key,
        "regions": region,
        "oddsFormat": "decimal",
        "dateFormat": "unix",
    }
    response = requests.get(url, params=params)
    if not response:
        handle_faulty_response(response)
    return response.json()


def process_data(matches: Iterable, include_started_matches: bool = True) -> Generator[dict, None, None]:
    for match in matches:
        start_time = int(match["commence_time"])
        if not include_started_matches and start_time < time.time():
            continue

        best_odds = {}
        for bookmaker in match.get("bookmakers", []):
            for outcome in bookmaker.get("markets", [{}])[0].get("outcomes", []):
                name = outcome["name"]
                price = outcome["price"]
                if name not in best_odds or price > best_odds[name][1]:
                    best_odds[name] = (bookmaker["title"], price)

        if len(best_odds) < 2:
            continue

        total_implied = sum(1 / odds for _, odds in best_odds.values())
        yield {
            "match_name": f"{match.get('home_team')} v. {match.get('away_team')}",
            "match_start_time": start_time,
            "hours_to_start": round((start_time - time.time()) / 3600, 2),
            "league": match.get("sport_key"),
            "best_odds": best_odds,
            "total_implied_odds": total_implied,
        }


def calculate_stakes(odds_dict: dict[str, tuple[str, float]], total_stake: int = 1000):
    """
    Calculates whole-number stakes for each outcome to ensure arbitrage with minimal suspicion.
    Returns a dict of outcome -> (bookie, odds, stake, profit)
    """
    # Step 1: Calculate raw inverse odds
    inverse_odds = {outcome: 1 / data[1] for outcome, data in odds_dict.items()}
    total_inverse = sum(inverse_odds.values())

    # Step 2: Calculate ideal (non-rounded) stake for each outcome
    ideal_stakes = {
        outcome: (inv / total_inverse) * total_stake
        for outcome, inv in inverse_odds.items()
    }

    # Step 3: Round stakes and track remainders for adjustment
    rounded_stakes = {
        outcome: int(stake)
        for outcome, stake in ideal_stakes.items()
    }

    # Step 4: Adjust for rounding error so total equals total_stake
    remainder = total_stake - sum(rounded_stakes.values())
    if remainder > 0:
        # Distribute remaining pounds to outcomes with highest remainder first
        stake_diffs = {
            outcome: ideal_stakes[outcome] - rounded_stakes[outcome]
            for outcome in rounded_stakes
        }
        for outcome in sorted(stake_diffs, key=stake_diffs.get, reverse=True):
            if remainder <= 0:
                break
            rounded_stakes[outcome] += 1
            remainder -= 1

    # Step 5: Calculate profits
    result = {}
    for outcome, (bookie, odd) in odds_dict.items():
        stake = rounded_stakes[outcome]
        payout = stake * odd
        profit = round(payout - total_stake, 2)
        result[outcome] = (bookie, odd, stake, profit)

    return result


def get_arbitrage_opportunities(key: str, region: str, cutoff: float = 0.01):
    sports = get_sports(key)
    print(f"Scanning {len(sports)} sports for arbitrage opportunities...\n")
    data = chain.from_iterable(get_data(key, sport, region=region) for sport in sports)
    matches = process_data(data)
    for match in matches:
        if 0 < match["total_implied_odds"] < 1 - cutoff:
            print(Fore.CYAN + Style.BRIGHT + f"\n🏆 Arbitrage found: {match['match_name']} ({match['league']})")
            print(Fore.YELLOW + f"🕒 Starts in {match['hours_to_start']}h")
            print(f"📉 Total implied odds: {round(match['total_implied_odds'], 4)}")
            print(f"{'-' * 55}")
        
            stakes = calculate_stakes(match["best_odds"])
            profits = []

            for outcome, (bookie, odd, stake, profit) in stakes.items():
                color = Fore.GREEN if profit >= 0 else Fore.RED
                profits.append(profit)
                print(f"{color}→ {outcome}: {Fore.MAGENTA}{bookie} @ {odd}{Style.RESET_ALL} | "
                    f"{Fore.BLUE}Stake: £{stake}{Style.RESET_ALL} | "
                    f"{color}Profit: £{profit}")

            print(f"{'-' * 55}")
            print(f"📈 Profit range: £{min(profits)} to £{max(profits)}\n")



# Example usage:
get_arbitrage_opportunities(API_KEY, REGION, CUTOFF)

