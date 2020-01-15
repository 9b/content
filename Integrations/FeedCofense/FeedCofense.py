import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *

""" IMPORTS """
from typing import List, Dict, Union, Optional, Tuple, Generator
import urllib3


# disable insecure warnings
urllib3.disable_warnings()

INTEGRATION_NAME = "Cofense Feed"
_RESULTS_PER_PAGE = 25


class Client(BaseClient):
    """Implements class for miners of Cofense feed over http/https."""

    available_fields = ["all", "malware", "phish"]

    CofenseToIndicator = {
        "IPv4 Address": FeedIndicatorType.IP,
        "Domain Name": FeedIndicatorType.Domain,
        "URL": FeedIndicatorType.URL,
        "Email": FeedIndicatorType.Email,
    }

    def __init__(
        self,
        url: str,
        auth: Tuple[str, str],
        threat_type: Optional[str] = None,
        verify: Optional[bool] = False,
        proxy: Optional[bool] = False,
    ):
        """Constructor of Client and BaseClient

        Arguments:
            url {str} -- url for Cofense feed
            auth {Tuple[str, str]} -- (username, password)

        Keyword Arguments:
            threat_type {Optional[str]} -- One of available_fields (default: {None})
            verify {Optional[bool]} -- Should verify certificate. (default: {False})
            proxy {Optional[bool]} -- Should (default: {False})
        """
        self.threat_type = (
            threat_type if threat_type in self.available_fields else "all"
        )

        # Request related attributes
        self.suffix = "/apiv1/threat/search/"

        super().__init__(url, verify=verify, proxy=proxy, auth=auth)

    def build_iterator(
        self, begin_time: Optional[int] = None, end_time: Optional[int] = None
    ) -> Generator:
        """builds iterator from given timestamp, or by url

        Keyword Arguments:
            begin_time {Optional[str, int]} --
                Where to start fetching.
                Timestamp represented in unix format. (default: {None})
            end_time {Optional[int]} --
                Time to stop fetching (if not supplied, will be time now).
                Timestamp represented in unix format. (default: {None}).
        Yields:
            Dict -- Threat from Cofense
        """
        # if not getting now
        if not end_time:
            end_time = get_now()

        payload = {
            "beginTimestamp": str(begin_time),
            "endTimestamp": str(end_time),
            "threatType": self.threat_type,
            "resultsPerPage": _RESULTS_PER_PAGE,
        }
        # For first fetch, there is only start time.
        if not begin_time:
            payload["beginTimestamp"] = str(end_time)
            del payload["endTimestamp"]

        cur_page = 0
        total_pages = 1
        demisto.debug(f"{INTEGRATION_NAME} - polling {begin_time}/{end_time}")
        while cur_page < total_pages:
            demisto.debug(f"{INTEGRATION_NAME} - polling {cur_page}/{total_pages}")

            payload["page"] = cur_page
            cjson = self._http_request("post", self.suffix, params=payload)

            data = cjson.get("data", {})
            if not data:
                return_error(f'{INTEGRATION_NAME} - no "data" in response')

            total_pages = data.get("page", {}).get("totalPages")
            if total_pages is None:
                return_error(f'{INTEGRATION_NAME} - no "totalPages" in response')

            demisto.debug(f"{INTEGRATION_NAME} - total_pages set to {total_pages}")

            threats = data.get("threats", [])
            for t in threats:
                yield t

            cur_page += 1

    @classmethod
    def _convert_block(cls, block: dict) -> Tuple[str, str]:
        """Gets a Cofense block from blockSet and enriches it.

        Args:
            block:

        Returns:
            indicator type, value
        """
        indicator = block.get("blockType", "")
        indicator = str(cls.CofenseToIndicator.get(indicator))
        # Only URL indicator has inside information in data_1
        if indicator == FeedIndicatorType.URL:
            value = block.get("data_1", {}).get("url")
        else:
            value = block.get("data_1")
        return indicator, value

    @classmethod
    def process_item(cls, threat: dict) -> List[dict]:
        """Gets a threat and proccesses them.

        Arguments:
            threat {dict} -- A threat from Cofense ("threats" key)

        Returns:
            list -- List of dicts representing indicators.

        Examples:
            >>> Client.process_item({"id": 123, "blockSet": [{"data_1": "ip", "blockType": "IPv4 Address"}]})
            [{'value': 'ip', 'type': 'IP', 'rawJSON': \
{'data_1': 'ip', 'blockType': 'IPv4 Address', 'value': 'ip', 'type': 'IP', 'threat_id': 123}}]
        """
        results = list()

        block_set: Optional[List[dict]] = threat.get("blockSet", None)
        thread_id = threat.get("id")
        if block_set:
            for block in block_set:
                indicator, value = cls._convert_block(block)
                block.update({"value": value, "type": indicator})
                block["threat_id"] = thread_id
                if indicator:
                    results.append(
                        {"value": value, "type": indicator, "rawJSON": block}
                    )

        else:
            demisto.debug(f'{INTEGRATION_NAME} - no "blockSet" in item')

        return results


def test_module(client: Client) -> str:
    """A simple test module

    Arguments:
        client {Client} -- Client derives from BaseClient

    Returns:
        str -- "ok" if succeeded, else raises a error.
    """
    client.build_iterator()
    return "ok"


def fetch_indicators_command(
    client: Client,
    begin_time: Optional[int] = None,
    end_time: Optional[int] = None,
    limit: Optional[int] = None,
) -> Union[Dict, List[Dict]]:
    """Fetches the indicators from client.

    Arguments:
        client {Client} -- Client derives from BaseClient
        indicator_type {str} -- [description]

    Keyword Arguments:
        update_context {bool} -- Should update context (default: {False})
        limit {int} -- Maximum indicators to fetch (when update_context is True)
        start_time {Optional[Union[str, int, dict]]} -- Last run object from Demisto or timestamp. (default: {None})

    Returns:
        Union[Dict, List[Dict]] -- [description]
    """
    indicators = list()
    for threat in client.build_iterator(begin_time=begin_time, end_time=end_time):
        # get maximum of limit
        new_indicators = client.process_item(threat)
        indicators.extend(new_indicators)
        if limit and limit < len(indicators):
            indicators = indicators[:limit]
            break
    return indicators


def parse_date_range_no_milliseconds(from_time: str) -> Tuple[int, int]:
    """Gets a range back and return time before the string and to now.
    Without milliseconds.

    Args:
        from_time:The date range to be parsed (required)

    Returns:
        start time, now

    Examples:
        >>> parse_date_range_no_milliseconds("3 days")
        (1578729151, 1578988351)
    """
    begin_time, end_time = parse_date_range(from_time, to_timestamp=True)
    return int(begin_time / 1000), int(end_time / 1000)


def get_indicators_command(client: Client, args: dict) -> Tuple[dict, list]:
    """Getting indicators into Demisto's incident.

    Arguments:
        client {Client} -- A client object
        args {dict} -- Usually demisto.args()

    Returns:
        Tuple[dict, list] -- context_output, human_readable
    """
    limit = int(args.get("limit", 10))
    from_time = args.get("from_time", "3 days")
    begin_time, end_time = parse_date_range_no_milliseconds(from_time)
    indicators = fetch_indicators_command(
        client, begin_time=begin_time, end_time=end_time, limit=limit
    )
    context_output = {"Cofense.Indicator": indicators[:limit]}
    return context_output, [indicator.get("rawJSON") for indicator in indicators]


def get_now() -> int:
    """Returns time now without milliseconds

    Returns:
        int -- time now without milliseconds.
    """
    return int(datetime.now().timestamp() / 1000)


def main(params: dict):
    """Main function

    Arguments:
        params {dict} -- demisto params
    """
    # handle params
    url = params.get("url", "https://threathq.com")
    credentials = params.get("credentials", {})
    auth = (credentials.get("identifier"), credentials.get("password"))
    verify = not params.get("insecure")
    proxy = bool(params.get("proxy"))
    threat_type = params.get("threat_type")
    client = Client(url, auth=auth, verify=verify, proxy=proxy, threat_type=threat_type)

    demisto.info(f"Command being called is {demisto.command()}")
    try:
        if demisto.command() == "test-module":
            return_outputs(test_module(client))

        elif demisto.command() == "fetch-indicators":
            end_time = get_now()
            last_fetch = demisto.getLastRun()
            begin_time = (
                last_fetch.get("timestamp") if isinstance(last_fetch, dict) else None
            )
            indicators = fetch_indicators_command(client, begin_time, end_time)
            # Send indicators to demisto
            for b in batch(indicators, batch_size=2000):
                demisto.createIndicators(b)
            # set last run is now
            demisto.setLastRun({"timestamp": end_time})
        elif demisto.command() == "get-indicators":
            # dummy command for testing
            context, indicators = get_indicators_command(client, demisto.args())
            human_readable = tableToMarkdown(
                f"Results from {INTEGRATION_NAME}:",
                indicators,
                [
                    "threat_id",
                    "type",
                    "value",
                    "impact",
                    "confidence",
                    "roleDescription",
                ],
            )
            return_outputs(human_readable, context)

    except Exception as err:
        return_error(str(err))


if __name__ in ["__main__", "builtin", "builtins"]:
    main(demisto.params())