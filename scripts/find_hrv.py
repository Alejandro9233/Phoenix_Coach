import json

with open('coros_scraped_data.json', 'r') as f:
    data = json.load(f)

def get_path(d, target, path=""):
    if isinstance(d, dict):
        for k, v in d.items():
            new_path = f"{path}.{k}" if path else k
            if k == target:
                print(new_path)
            get_path(v, target, new_path)
    elif isinstance(d, list):
        for i, v in enumerate(d):
            get_path(v, target, f"{path}[{i}]")

get_path(data, 'sleepHrvList')
get_path(data, 'avgSleepHrv')
