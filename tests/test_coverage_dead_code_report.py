import json

from scripts.coverage_dead_code_report import build_report, load_coverage_files, write_report


def test_coverage_dead_code_report_classifies_candidates(tmp_path):
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(
        json.dumps({
            "files": {
                "src/api/services/unused.py": {
                    "summary": {
                        "percent_covered": 0,
                        "missing_lines": 10,
                        "covered_lines": 0,
                    },
                },
                "src/api/services/partial.py": {
                    "summary": {
                        "percent_covered": 12.5,
                        "missing_lines": 7,
                        "covered_lines": 1,
                    },
                },
                "src/api/services/covered.py": {
                    "summary": {
                        "percent_covered": 95,
                        "missing_lines": 1,
                        "covered_lines": 19,
                    },
                },
            },
        }),
        encoding="utf-8",
    )

    files = load_coverage_files(coverage_json, threshold=20)
    by_path = {item.path: item for item in files}

    assert "src/api/services/unused.py" in by_path
    assert by_path["src/api/services/unused.py"].category == "zero_coverage"
    assert "src/api/services/partial.py" in by_path
    assert by_path["src/api/services/partial.py"].category == "low_coverage"
    assert "src/api/services/covered.py" not in by_path

    report = build_report(files, threshold=20, coverage_json=coverage_json)
    assert "Zero Coverage" in report
    assert "Low Coverage" in report

    output = tmp_path / "dead-code-candidates.md"
    write_report(files, output, threshold=20, coverage_json=coverage_json)
    assert output.exists()
