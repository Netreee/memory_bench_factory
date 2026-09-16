"""Dependency-free grading record contract shared by caches and consumers."""
JUDGE_VERSION = "typed-primary-v3"


def is_scored(record: dict) -> bool:
    grade = record.get("judgement") or record
    return (isinstance(grade, dict) and grade.get("version") == JUDGE_VERSION
            and all(isinstance(grade.get(key), str) and grade[key].strip() for key in ("path", "reason"))
            and grade.get("verdict") in {"correct", "incorrect"}
            and type(grade.get("correct")) is bool
            and grade["correct"] == (grade["verdict"] == "correct")
            and ("judgement" not in record or record.get("correct") is grade["correct"])
            and not record.get("error") and not record.get("judge_error"))
