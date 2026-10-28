"""Isolated HTTP worker for cancellable JPL and bounded satellite queries."""
import json
import sys
import requests


def main():
    request=json.load(sys.stdin)
    try:
        with requests.get(request['url'],params=request.get('params'),headers={'User-Agent':'Stellina-AstroAnalyse/1.0 (local astronomy analysis)'},timeout=(10,40)) as response:
            response.raise_for_status()
            result=dict(ok=True,payload=response.text if request.get('text') else response.json())
    except requests.Timeout:result=dict(ok=False,kind='timeout')
    except requests.RequestException:result=dict(ok=False,kind='request')
    except ValueError:result=dict(ok=False,kind='format')
    json.dump(result,sys.stdout)


if __name__=='__main__':main()
