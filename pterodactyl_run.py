import asyncio
import os
import sys

# Add current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from batch_run import main as run_batch
except ImportError as e:
    print(f"Error importing batch_run: {e}")
    sys.exit(1)

async def bot_loop():
    print("====================================================")
    print("Starting background video uploader loop on HeavenCloud...")
    print("Checking for new videos in a continuous 10s loop 24/7...")
    print("====================================================")
    
    while True:
        try:
            print("\n--- Starting batch upload run ---")
            await run_batch()
            print("--- Batch upload run completed. Sleeping for 10 seconds ---")
        except Exception as e:
            print(f"Error during batch execution: {e}", file=sys.stderr)
            await asyncio.sleep(30) # Sleep longer on error to prevent CPU thrashing
            continue
            
        await asyncio.sleep(10) # 10 seconds interval for live uploader loop

if __name__ == "__main__":
    try:
        asyncio.run(bot_loop())
    except KeyboardInterrupt:
        print("\nLoop stopped by user.")
