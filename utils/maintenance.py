import asyncio
from sandbox.kernel import cleanup_user_kernels
from cache.state import RedisStateManager


async def redis_cleanup_listener():
    """
    Monitor kernel TTLs and cleanup expired kernels.
    Now using in-memory state manager instead of Redis pub/sub.
    """
    redis_state = RedisStateManager()

    while True:
        try:
            await asyncio.sleep(10)  # Check every 10 seconds

            # Get all kernel users with their TTLs
            user_ttls = redis_state.get_all_kernel_users_with_ttl()

            # Check for expired or about-to-expire kernels
            for claude_id, ttl in user_ttls.items():
                # TTL <= 0 means expired
                if ttl <= 0:
                    print(f"🕐 Kernel TTL expired for user {claude_id}")
                    try:
                        cleanup_user_kernels(claude_id)
                        print(f"✅ Cleaned up local kernel for {claude_id}")
                    except Exception as e:
                        print(f"❌ Error cleaning up kernel for {claude_id}: {e}")

        except Exception as e:
            print(f"Error in cleanup listener: {e}")
            await asyncio.sleep(1)
