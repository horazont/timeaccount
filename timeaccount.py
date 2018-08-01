#!/usr/bin/python3
import ast
import itertools
import pathlib
import re
import sys

from datetime import datetime, timedelta

import babel.dates


SET_RE = re.compile(r"set\s+(?P<key>[0-9A-Za-z_]+)\s+(?P<value>.+)")
START_END_RE = re.compile(r"^\s*(?P<key>start|end)\s+(?P<value>.+)$")
COMMENT_STRIP_RE = re.compile(r"([^#]*)#.*")
RANGE_RE = re.compile(r"^\s*(?P<start>[0-9.: -]+)\s*(--|–)\s*(?P<end>[0-9.: -]+|now)\s*(?P<note>.*)$")
KEYED_NOTE_RE = re.compile("^\[(?P<id>[0-9]+)\]\s*(?P<note>.*)$")
SQUASH_RE = re.compile(r"^\s*squashed\s+(?P<timedelta>(?P<hours>[0-9]+):(?P<minutes>[0-9]{2}):(?P<seconds>[0-9]{2}(\.[0-9]*)?))\s*$")

WORKDAYS = [0, 1, 2, 3, 4]


def process_set(match, filedata):
    d = match.groupdict()
    filedata.setdefault("settings", {})[d["key"]] = ast.literal_eval(
        d["value"]
    )


def process_start_end(match, filedata):
    d = match.groupdict()
    date = parse_date(d["value"])
    key = d["key"]
    dt = datetime(year=date.year, month=date.month, day=date.day)
    if key == "end":
        dt += timedelta(days=1)
    filedata[d["key"]] = dt


def process_range(match, filedata):
    d = match.groupdict()
    start = parse_datetime(d["start"],
                           filedata.setdefault("state", {}).get("prevdate"))
    end = parse_datetime(d["end"], start)

    keyed_note_match = KEYED_NOTE_RE.match(d["note"].strip())
    if keyed_note_match is not None:
        note_d = keyed_note_match.groupdict()
        id_ = int(note_d["id"])
        filedata.setdefault("idmap", {}).setdefault(id_,
                                                    note_d["note"])
    else:
        id_ = None

    filedata.setdefault("ranges", []).append(
        (start, end, id_)
    )
    filedata["state"]["prevdate"] = start


def process_squash(match, filedata):
    d = match.groupdict()
    td = timedelta(
        hours=int(d["hours"]),
        minutes=int(d["minutes"]),
        seconds=float(d["seconds"]),
    )
    filedata.setdefault("squashes", []).append(
        td
    )


PARSER = [
    (START_END_RE, process_start_end),
    (SET_RE, process_set),
    (RANGE_RE, process_range),
    (SQUASH_RE, process_squash),
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
    if len(parts) == 1 and parts[0] == "now":
        return datetime.now()
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
    for file in sorted(p.iterdir()):
        if file.name.endswith("~"):
            continue
        if file.is_dir():
            continue
        try:
            filedata = read_file(file.open("r"))
        except ParserError as exc:
            print("while reading {}".format(file), file=sys.stderr)
            print("in line {}: {}".format(exc.lineno, exc.linecontent))
            if exc.msg:
                print("  {}".format(exc.msg))
            if exc.__context__:
                print("  {}".format(exc.__context__))
            continue
        filedata["name"] = file
        yield filedata


def finalize_data(data):
    data["daily_ids"] = {}

    total_hours = 0
    for entry in data.get("ranges", []):
        start, end, id_ = entry
        this_hours = (end - start).total_seconds() / 3600
        total_hours += this_hours
        if id_ is not None:
            day = start.replace(hour=0, minute=0, second=0, microsecond=0)
            daymap = data["daily_ids"].setdefault(day, {})
            daymap.setdefault(id_, 0)
            daymap[id_] += this_hours

    for squash in data.get("squashes", []):
        total_hours += squash.total_seconds() / 3600

    data["total_hours"] = total_hours

    if data["end"] is None or data["end"] >= datetime.now():
        today = datetime.now().replace(hour=0, minute=0, second=0,
                                       microsecond=0)
        end_of_day = today + timedelta(days=1)
        end_of_week = today + timedelta(days=7-(today.weekday()))

        if data["end"] is None or end_of_week <= data["end"]:
            weeks = get_weeks(data["start"], end_of_week)
            data["hours_to_weekend"] = weeks * data["settings"]["hours_per_week"]

        if (data["end"] is None or end_of_day <= data["end"]) and data["settings"]["hours_per_day"]:
            days = (end_of_day - data["start"]).days
            data["hours_to_night"] = days * data["settings"]["hours_per_day"]
    if data["end"] is not None:
        weeks = get_weeks(data["start"], data["end"])
        data["hours_to_end"] = weeks * data["settings"]["hours_per_week"]


def startkey(r):
    return r[0]


def startdaykey(r):
    return r[0].replace(hour=0, minute=0, second=0, microsecond=0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--daily",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--squash",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "indir",
    )

    args = parser.parse_args()

    now = datetime.utcnow()

    for filedata in read_dir(args.indir):
        finalize_data(filedata)

        if args.daily and (filedata["end"] is None or filedata["end"] >= now):
            prevmonth = None
            monthtotal = timedelta()
            for day, subranges in itertools.groupby(
                    sorted(filedata["ranges"], key=startkey),
                    startdaykey):

                if prevmonth is None:
                    prevmonth = day.replace(day=1)
                if day.replace(day=1) != prevmonth:
                    prevmonth = day.replace(day=1)
                    print("month:", monthtotal)
                    monthtotal = timedelta()

                daytotal = sum((r[1]-r[0] for r in subranges), timedelta())
                try:
                    daymap = filedata["daily_ids"][day]
                except KeyError:
                    pass
                else:
                    for id_, hours in sorted(daymap.items()):
                        print(day.date(), "{:04d} {}".format(id_, timedelta(hours=hours)))
                print(day.date(), "total", daytotal)

                monthtotal += daytotal

        try:
            until_eod = filedata["hours_to_night"]
        except KeyError:
            pass
        else:
            print("in {}: {}h missing until end of day".format(
                filedata["name"].parts[-1],
                timedelta(hours=until_eod - filedata["total_hours"])
            ))

        try:
            until_weekend = filedata["hours_to_weekend"]
        except KeyError:
            pass
        else:
            print("in {}: {}h missing until weekend".format(
                filedata["name"].parts[-1],
                timedelta(hours=until_weekend - filedata["total_hours"])
            ))

        try:
            until_eoc = filedata["hours_to_end"]
        except KeyError:
            pass
        else:
            if filedata["end"] >= datetime.now():
                print("in {}: {}h missing until end of contract".format(
                    filedata["name"].parts[-1],
                    timedelta(hours=until_eoc - filedata["total_hours"])
                ))

        if args.squash:
            hours, rem = divmod(filedata["total_hours"] * 3600, 3600)
            minutes, seconds = divmod(rem, 60)
            print("{}: squash {:02.0f}:{:02.0f}:{:06.3f}".format(
                filedata["name"].parts[-1],
                hours, minutes, seconds,
            ))
