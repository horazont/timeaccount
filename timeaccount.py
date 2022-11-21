#!/usr/bin/python3
import ast
import collections
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
KEYED_NOTE_RE = re.compile(r"^\[(?P<id>[0-9]+)(/(?P<task>[\w\d\s]+))?\]\s*(?P<note>.*)$")
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
        id_ = int(note_d["id"]), note_d["task"]
        filedata.setdefault("idmap", {}).setdefault(id_,
                                                    note_d["note"])
    else:
        id_ = (None, None)

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


def get_workdays(from_start, to_end):
    from_start = from_start.replace(hour=0, minute=0, second=0, microsecond=0)
    to_end = to_end.replace(hour=0, minute=0, second=0, microsecond=0)
    if from_start == to_end:
        return 0
    elif from_start >= to_end:
        return -get_workdays(to_end, from_start)

    days = 0

    if from_start.weekday() != 0:
        if from_start.weekday() < 5:
            days += min(5 - from_start.weekday(),
                        (to_end - from_start).days)

        # it’s on a monday now
        from_start += timedelta(days=7-from_start.weekday())
        assert from_start.weekday() == 0

    if from_start >= to_end:
        return days

    if to_end.weekday() != 0:
        if to_end.weekday() > 5:
            to_end -= timedelta(days=to_end.weekday() - 5)
        days += min(to_end.weekday(),
                    (to_end - from_start).days)
        to_end -= timedelta(days=to_end.weekday())
        assert to_end.weekday() == 0

    weeks = get_weeks(from_start, to_end)
    assert int(weeks) == weeks
    days += int(weeks) * 5
    return days



assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 2)) == 1
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 3)) == 2
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 4)) == 3
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 5)) == 3
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 6)) == 3
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 7)) == 4
assert get_workdays(datetime(2018, 8, 1), datetime(2018, 8, 13)) == 8
assert get_workdays(datetime(2018, 8, 13), datetime(2018, 8, 20)) == 5


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
            # weeks = get_weeks(data["start"], end_of_week)
            # data["hours_to_weekend"] = weeks * data["settings"]["hours_per_week"]
            days_until_eow = get_workdays(data["start"], end_of_week)
            data["hours_to_weekend"] = days_until_eow * data["settings"]["hours_per_day"]

        if (data["end"] is None or end_of_day <= data["end"]) and data["settings"]["hours_per_day"] is not None:
            days = get_workdays(data["start"], end_of_day)
            data["hours_to_night"] = days * data["settings"]["hours_per_day"]
    if data["end"] is not None:
        weeks = get_weeks(data["start"], data["end"])
        data["hours_to_end"] = weeks * data["settings"]["hours_per_week"]


def startkey(r):
    return r[0]


def startdaykey(r):
    return r[0].replace(hour=0, minute=0, second=0, microsecond=0)


def round_timedelta(td):
    return timedelta(seconds=round(td.total_seconds()))


def hour_timedelta(td, precision=0):
    hours, rem = divmod(td.total_seconds(), 3600)
    minutes, seconds = divmod(rem, 60)
    return "{{:.0f}}:{{:02.0f}}:{{:0{}.{}f}}".format(precision+(3 if precision > 0 else 2), precision).format(
        hours, minutes, seconds,
    )


def dump_project_hours(mapping):
    for id_, hours in sorted(mapping.items()):
        print("  ID {:04d}  {:.2f}".format(id_, hours.total_seconds() / 3600))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--daily",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--monthly",
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
                    monthly_project_hours = collections.defaultdict(timedelta)
                if day.replace(day=1) != prevmonth:
                    prevmonth = day.replace(day=1)
                    print("month:", monthtotal)
                    if args.monthly:
                        dump_project_hours(monthly_project_hours)
                    monthtotal = timedelta()
                    monthly_project_hours = collections.defaultdict(timedelta)

                daytotal = sum((r[1]-r[0] for r in subranges), timedelta())
                try:
                    daymap = filedata["daily_ids"][day]
                except KeyError:
                    pass
                else:
                    for (id_, task), hours in sorted(daymap.items(), key=lambda x: ((x[0][0], x[0][1] or ""), x[1])):
                        monthly_project_hours[id_, task] += timedelta(hours=hours)
                        print(day.date(), "{:04d}{} {}".format(id_, "/{}".format(task) if task else "", timedelta(hours=hours)))
                print(day.date(), "total", daytotal)

                monthtotal += daytotal

            if prevmonth is not None and args.monthly:
                print("month:", monthtotal)
                dump_project_hours(monthly_project_hours)

        try:
            until_eod = filedata["hours_to_night"]
        except KeyError:
            pass
        else:
            until_eod_td = timedelta(hours=until_eod - filedata["total_hours"])
            if until_eod_td >= timedelta():
                endtime = " (~= {})".format(
                    (datetime.now() + until_eod_td).time().replace(microsecond=0)
                )
                print("in {}: {}h missing until end of day{}".format(
                    filedata["name"].parts[-1],
                    hour_timedelta(round_timedelta(until_eod_td)),
                    endtime,
                ))
            else:
                print("in {}: {}h overtime today".format(
                    filedata["name"].parts[-1],
                    hour_timedelta(round_timedelta(-until_eod_td)),

                ))


        try:
            until_weekend = filedata["hours_to_weekend"]
        except KeyError:
            pass
        else:
            print("in {}: {}h missing until weekend".format(
                filedata["name"].parts[-1],
                hour_timedelta(round_timedelta(timedelta(hours=until_weekend - filedata["total_hours"])))
            ))

        try:
            until_eoc = filedata["hours_to_end"]
        except KeyError:
            pass
        else:
            if filedata["end"] >= datetime.now():
                print("in {}: {}h missing until end of contract".format(
                    filedata["name"].parts[-1],
                    hour_timedelta(round_timedelta(timedelta(hours=until_eoc - filedata["total_hours"])))
                ))

        if args.squash:
            print("{}: squash {}".format(
                filedata["name"].parts[-1],
                hour_timedelta(timedelta(hours=filedata["total_hours"]))
            ))
