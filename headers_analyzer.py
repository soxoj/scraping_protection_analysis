#!/usr/bin/env python3
"""
1. Open desired page in Google Chrome
2. Go to Developer Tools
3. Find desired request and do "Copy" => "Copy as Node.js fetch"
4. Save it in the file
5. Do ./analyzer.py <FILE>

You will get minimal headers set to get needed content.
"""

import json
import time
import itertools
import re
import requests
import sys
import difflib
from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm


MINIMAL_RESP_CHECK_N = 10
DIST_ROUND_PRECISION = 3
EXPECTED_TEXT_ANOMALY = False


class ResponseResult:
    def __init__(self, resp: requests.models.Response, req_h: dict):
        self.response = resp
        self.req_headers = req_h

    def __repr__(self):
        return f'<{self.response.status_code}> <{len(self.req_headers)} headers>'


def length_ratio(a, b):
    a_len = len(a.response.text)
    b_len = len(b.response.text)

    return min(a_len, b_len) / max(a_len, b_len)

def difflib_ratio(a, b):
    a_text = a.response.text
    b_text = b.response.text
    return difflib.SequenceMatcher(a=a_text, b=b_text).ratio()


def check_headers(req_fun, dist_fun, headers, ratio, ref, expected_text):
    res = {}

    keys = list(headers.keys())
    keys_to_del = []

    for key in tqdm(keys):
        headers_copy = {k:v for k,v in headers.items() if k != key}

        r = req_fun(headers_copy)
        key_resp = ResponseResult(r, headers_copy)

        distance = round(dist_fun(ref, key_resp), DIST_ROUND_PRECISION)
        is_anomaly = distance < ratio

        is_expected_text_present = expected_text and expected_text in r.text

        data = {
            'code': r.status_code,
            'len': len(r.text),
            'diff': distance,
            'is_anomaly': is_anomaly,
            'resp': key_resp,
            'is_present_text': is_expected_text_present,
        }

        sys.stdout.write('\x1b[1K\r')
        print(f'{Fore.YELLOW}{key}{Style.RESET_ALL}')
        print(data)

        if is_anomaly or (not is_expected_text_present and EXPECTED_TEXT_ANOMALY):
            res[key] = data
        else:
            keys_to_del.append(key)

    new_headers = dict(headers)
    for k in keys_to_del: 
        del new_headers[k]

    return new_headers, res


def save_resp(filename, r):
    with open(filename, 'w', encoding='utf8') as f:
        f.write(r.response.text)
        print('Saved.')


if __name__ == '__main__':
    colorama_init(autoreset=True)
    if len(sys.argv) == 1:
        print(f'{Fore.RED}Usage: ./analyzer.py <FILE>{Style.RESET_ALL}')

    print('Analysis started...')

    fetch_file_text = open(sys.argv[1]).read()
    stripped_text = fetch_file_text[6:-3]

    url = stripped_text.split(', ')[0].strip('"')
    json_struct = stripped_text.split(', ', 1)[1]
    req_json = json.loads(json_struct)

    headers = req_json['headers']
    if not 'user-agent' in list(map(str.lower, headers.keys())):
        print('Add default User-Agent...')
        headers['user-agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36'

    method = req_json['method']
    body = req_json['body']

    requests_method = requests.__dict__[method.lower()]
    expected_text = input('Enter text you expected will be in response: ')

    r = requests_method(url, headers=headers, data=body)

    print(f'Status code of response: {r.status_code}')
    print(f'Response text beginning: {Fore.YELLOW}{r.text[:100]}{Style.RESET_ALL}...')

    if expected_text != '' and not expected_text in r.text:
        print(f'Can\'t find {expected_text} in response text!')
        if input('Is this okay? [y/n] ') == 'n':
            filename = sys.argv[1] + '_invalid.txt'
            print(f'Invalid response will be saved to file {Fore.GREEN}{filename}{Style.RESET_ALL} ... ', end='')
            save_resp(filename, ResponseResult(r, headers))
            print('Exiting. :(')
            sys.exit(0)

    reference_resp = ResponseResult(r, dict(headers))
    filename = sys.argv[1] + '_ref.txt'
    print(f'Reference response will be saved to file {Fore.GREEN}{filename}{Style.RESET_ALL} ... ', end='')
    save_resp(filename, reference_resp)

    reference_text = r.text
    distance_fun = difflib_ratio

    print(f'Length of reference response text is {len(reference_text)}')
    print('Check for minimal response text difference...')

    responses = []
    for _ in tqdm(range(MINIMAL_RESP_CHECK_N)):
        rr = requests_method(url, headers=headers, data=body)
        responses.append(ResponseResult(rr, dict(headers)))

    print('Calculating differences...')

    if len(responses[0].response.text) > 10000:
        print('Automatically use length distance function.')
        distance_fun = length_ratio
        probe_ratio = length_ratio(reference_resp, responses[0])
    else:        
        probe_start_t = time.time()
        probe_ratio = distance_fun(reference_resp, responses[0])
        probe_end_t = time.time()

        if probe_end_t - probe_start_t > 0.5: # three seconds? too much to wait
            print('Too slow text distance function, will check length only!')
            distance_fun = length_ratio
            probe_ratio = length_ratio(reference_resp, responses[0])

    ratios = [probe_ratio]
    for resp in responses[1:]:
        ratio = distance_fun(reference_resp, resp)
        ratios.append(ratio)

    reference_ratio = round(min(ratios), DIST_ROUND_PRECISION)

    print(f'Reference ratio is {reference_ratio}')
    print(f'Now trying to make requests without some headers...')

    req_fun = lambda x: requests_method(url, headers=x, data=body)

    minimal_headers, anomalies = check_headers(headers=headers, ratio=reference_ratio,
                                               dist_fun=distance_fun, ref=reference_resp,
                                               req_fun=req_fun, expected_text=expected_text)

    if len(minimal_headers) == len(headers):
        print('All responses are different!')
        sys.exit(0)

    print('Minimal headers are: ')
    print(json.dumps(minimal_headers, indent=4))

    for k, v in anomalies.items():
        filename = sys.argv[1] + '_' + k + '.txt'
        diff = v['diff']
        l = v['len']
        has_expected_test = v['is_present_text']
        print(f'Response without {k} header will be saved to file '
              f'{Fore.GREEN}{filename}{Style.RESET_ALL} '
              f'(diff {diff}, len {l}, expected text: {has_expected_test})... ', end='')
        save_resp(filename, v['resp'])
