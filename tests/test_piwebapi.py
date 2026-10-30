from pi_compression_tuner.sources.piwebapi import PIWebAPI, PointRef, config_from_attributes, series_from_items


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    """Answers GETs from a dict of path -> body (or callable(params) -> body). Refuses writes."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.verify = True
        self.calls = []

    def get(self, url, params=None, timeout=None):
        path = url.split("/piwebapi/", 1)[1]
        self.calls.append((path, params))
        body = self.routes[path]
        return FakeResponse(body(params) if callable(body) else body)

    def post(self, *a, **k):
        raise AssertionError("unexpected POST")

    def put(self, *a, **k):
        raise AssertionError("writes are not allowed")

    patch = delete = put


def _api(routes):
    return PIWebAPI("https://pi/piwebapi", session=FakeSession(routes))


def test_attributes_to_config():
    api = _api({"points/W1/attributes": {"Items": [
        {"Name": "compdev", "Value": 0.5}, {"Name": "excdev", "Value": 0.25}, {"Name": "compressing", "Value": 0},
        {"Name": "span", "Value": 200}, {"Name": "step", "Value": 1}]}})
    c = api.config(PointRef("A", "W1", ""))
    assert (c.compdev, c.excdev, c.compressing, c.span, c.step) == (0.5, 0.25, False, 200.0, True)


def test_recorded_pages_until_short_page():
    ts = [f"2026-01-01T00:00:{i:02d}Z" for i in range(10)]

    def recorded(params):
        start = params["startTime"]
        i0 = 0 if start == "*-1d" else ts.index(start)
        items = [{"Timestamp": t, "Value": float(i0 + k), "Good": True} for k, t in enumerate(ts[i0:i0 + params["maxCount"]])]
        return {"Items": items}

    api = _api({"streams/W1/recorded": recorded})
    s = api.recorded(PointRef("A", "W1", ""), "*-1d", "*", page_size=4)
    assert len(s) == 10
    assert s.v.tolist() == [float(i) for i in range(10)]


def test_series_drops_bad_and_digital_values():
    s = series_from_items([
        {"Timestamp": "2026-01-01T00:00:00Z", "Value": 1.0, "Good": True},
        {"Timestamp": "2026-01-01T00:00:01Z", "Value": {"Name": "Shutdown", "Value": 254}, "Good": False},
        {"Timestamp": "2026-01-01T00:00:02Z", "Value": 2.0, "Good": False},
        {"Timestamp": "2026-01-01T00:00:03.5Z", "Value": 3, "Good": True},
    ])
    assert s.v.tolist() == [1.0, 3.0]


def test_missing_attributes_use_defaults():
    c = config_from_attributes({"compdev": None})
    assert c.compmax == 28800.0 and c.compressing


def test_lookups_by_path():
    api = _api({"dataservers": lambda p: {"WebId": "DS1"} if p == {"path": "\\\\SRV"} else {},
                "points": lambda p: {"Name": "A", "WebId": "W1", "Path": p["path"]}})
    api.check("SRV")
    ref = api.point("SRV", "A")
    assert (ref.name, ref.web_id, ref.path) == ("A", "W1", "\\\\SRV\\A")
