#!/usr/bin/python3
import ast
import pathlib
import re

from datetime import datetime, timedelta

import babel.dates


SET_RE = re.compile(r"set\s+(?P<key>[0-9A-Za-z_]+)\s+(?P<value>.+)")
START_END_RE = re.compile(r"(?P<key>start|end)\s+(?P<value>.+)")
COMMENT_STRIP_RE = re.compile(r"(.*)#.*")
RANGE_RE = re.compile(r"\s*(?P<start>[0-9.: -]+)\s*--\s*(?P<end>[0-9.: -]+)\s*(?P<note>.*)")

WORKDAYS = [0, 1, 2, 3, 4]


def process_set(match, filedata):
    d = match.groupdict()
    filedata.setdefault("settings", {})[d["key"]] = ast.literal_eval(
        d["value"]
    )


def process_start_end(match, filedata):
    d = match.groupdict()
    date = parse_date(d["value"])
    filedata[d["key"]] = datetime(year=date.year, month=date.month,
                                  day=date.day)


def process_range(match, filedata):
    d = match.groupdict()
    start = parse_datetime(d["start"],
                           filedata.setdefault("state", {}).get("prevdate"))
    end = parse_datetime(d["end"], start)
    filedata.setdefault("ranges", []).append(
        (start, end)
    )
    filedata["state"]["prevdate"] = start


PARSER = [
    (START_END_RE, process_start_end),
    (SET_RE, process_set),
    (RANGE_RE, process_range),
]


def parse_time(s):
    try:
        return babel.dates.parse_time(s)
    except IndexError:
        return babel.dates.parse_time(s+":00")


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return babel.dates.parse_date(s)


def parse_datetime(s, reference_datetime=None):
    parts = s.strip().split(" ", 1)
    if reference_datetime and len(parts) == 1:
        date = reference_datetime.date()
        time = parse_time(parts[0])
    else:
        date = parse_date(parts[0])
        time = parse_time(parts[1])
    return datetime(date.year, date.month, date.day,
                    time.hour, time.minute, time.second)


def get_weeks(from_start, to_end):
    from_start = from_start.replace(hour=0, minute=0, second=0, microsecond=0)
    to_end = to_end.replace(hour=0, minute=0, second=0, microsecond=0)

    return (to_end - from_start).days / 7


class ParserError(ValueError):
    def __init__(self, lineno, linecontent, msg=None):
        super().__init__("failed to parse line {}".format(lineno))
        self.lineno = lineno
        self.linecontent = linecontent
        self.msg = msg


def read_file(initer):
    filedata = {
        "settings": {},
        "ranges": [],
        "end": None,
    }

    for i, line in enumerate(initer):
        m = COMMENT_STRIP_RE.match(line)
        if m:
            line = m.group(1)
        line = line.strip()
        if not line:
            continue
        for regex, handler in PARSER:
            m = regex.match(line)
            if m is None:
                continue
            try:
                handler(m, filedata)
            except ValueError:
                raise ParserError(i, line)
            break
        else:
            raise ParserError(i, line, "no parser for line")

    return filedata


def read_dir(path):
    p = pathlib.Path(path).absolute()
    for file in p.iterdir():
        if file.name.endswith("~"):
            continue
        filedata = read_file(file.open("r"))
        filedata["name"] = file
        yield filedata


def finalize_data(data):
    total_hours = 0
    for entry in data["ranges"]:
        start, end = entry
        total_hours += (end - start).total_seconds() / 3600

    data["total_hours"] = total_hours

    if data["end"] is None:
        end_of_week = datetime.now().replace(hour=0, minute=0, second=0,
                                             microsecond=0)
        end_of_week += timedelta(days=7-(end_of_week.weekday()))
        weeks = get_weeks(data["start"], end_of_week)
        data["hours_to_weekend"] = weeks * data["settings"]["hours_per_week"]
    else:
        weeks = get_weeks(data["start"], data["end"])
        data["hours_to_end"] = weeks * data["settings"]["hours_per_week"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "indir",
    )

    args = parser.parse_args()

    for filedata in read_dir(args.indir):
        finalize_data(filedata)
        if filedata["end"] is None:
            print("in {}: {:.2f}h missing until weekend".format(
                filedata["name"].parts[-1],
                filedata["hours_to_weekend"] - filedata["total_hours"]
            ))
