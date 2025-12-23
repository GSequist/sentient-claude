from circadian.circadian_stimuli import CIRCADIAN_STIMULI
from cache.state import RedisStateManager
import asyncio


async def circadian_monitor(claude_id: str, time_scale: int = 60):
    """
    Monitor Claude's internal time and inject circadian stimuli

    time_scale: real seconds per Claude hour
    - Default 60: 1 real minute = 1 Claude hour (24 minutes = full day)
    """

    redis_state = RedisStateManager()
    last_injected_hour = None

    while True:
        # Get Claude's current hour (0-23)
        current_hour = redis_state.get_claude_hour(claude_id)

        # Inject stimulus if we hit a trigger hour
        if current_hour in CIRCADIAN_STIMULI and current_hour != last_injected_hour:

            stimulus = CIRCADIAN_STIMULI[current_hour]

            redis_state.add_stimulus(
                claude_id=claude_id,
                content=stimulus["prompt"],
                source="circadian",
                energy_level=stimulus["energy_level"],
            )

            last_injected_hour = current_hour

        # Check every 10 real seconds
        await asyncio.sleep(10)
