import time
import requests

# ─────────────────────────────────────────────────────────────
#  CONFIG — Enter your details here
# ─────────────────────────────────────────────────────────────
GITHUB_USERNAME = "vasistadronadula"
REPO_NAME       = "xmens-bot"
WORKFLOW_FILE   = "upload.yml"  # name of the workflow file

# Generate a classic token here: https://github.com/settings/tokens
# It needs the "repo" and "workflow" scopes.
GITHUB_TOKEN    = "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN"

def trigger_github_workflow():
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    data = {
        "ref": "master"  # run on the master branch
    }
    
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Triggering GitHub Action workflow...")
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            print("✅ Success! Workflow triggered successfully.")
        else:
            print(f"❌ Failed to trigger. Status Code: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    if GITHUB_TOKEN == "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN":
        print("Please edit this script and set GITHUB_TOKEN first!")
        print("You can generate one from: https://github.com/settings/tokens (select 'repo' and 'workflow' scopes)")
    else:
        # Trigger immediately
        trigger_github_workflow()
        
        # Loop to trigger every 5 minutes (300 seconds)
        print("\nStarting trigger loop. Press Ctrl+C to stop.")
        while True:
            try:
                time.sleep(300)
                trigger_github_workflow()
            except KeyboardInterrupt:
                print("\nStopping trigger script.")
                break
