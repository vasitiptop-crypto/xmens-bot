import gradio as gr
import asyncio
import threading
import sys
import os

# Add current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from batch_run import main as run_batch
except ImportError as e:
    print(f"Error importing batch_run: {e}")
    run_batch = None

def run_bot_in_background():
    # Set up a new async event loop for the background thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def bot_loop():
        bot_token = os.environ.get("BOT_TOKEN")
        channel_id = os.environ.get("CHANNEL_ID")
        if not bot_token or not channel_id:
            print("WARNING: BOT_TOKEN or CHANNEL_ID not set in Space Secrets.")
            
        print("Starting background video uploader loop...")
        while True:
            try:
                print("\n--- Starting batch upload run ---")
                if run_batch:
                    await run_batch()
                else:
                    print("ERROR: run_batch function is not imported.")
                print("--- Batch upload run completed. Sleeping for 5 minutes ---")
            except Exception as e:
                print(f"Error during batch execution: {e}", file=sys.stderr)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(300) # Sleep for 5 minutes (300 seconds)

    loop.run_until_complete(bot_loop())

# Start the bot thread immediately upon web app start
bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
bot_thread.start()

# Define the Gradio dashboard interface
with gr.Blocks(title="Telegram Video Uploader") as demo:
    gr.Markdown("# 🤖 Telegram Video Uploader status: Running")
    gr.Markdown("This space runs the bot uploader loop in the background every 5 minutes.")
    gr.Markdown("To prevent this free space from sleeping, configure a free pinging service (like UptimeRobot) to hit this space URL every 30 minutes.")

# Gradio defaults to port 7860
demo.launch(server_name="0.0.0.0", server_port=7860)
