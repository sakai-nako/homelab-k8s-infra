"""bridge 中核ロジックのユニットテスト (ネットワーク不要).

ForgejoClient を Fake に差し替え、Alertmanager webhook payload (version 4) を
模した dict で「1 グループ = 1 open Issue」の状態遷移を検証する。
"""

import unittest

from am_forgejo_bridge.bridge import (
    Bridge,
    build_title,
    group_digest,
    group_marker,
    parse_fps,
)


def _payload(status="firing", alerts=None, group_key='{}:{alertname="X"}', group_labels=None):
    return {
        "version": "4",
        "status": status,
        "groupKey": group_key,
        "groupLabels": group_labels if group_labels is not None else {"alertname": "X"},
        "alerts": alerts if alerts is not None else [],
    }


def _alert(status="firing", fingerprint="fp1", alertname="X", **labels):
    return {
        "status": status,
        "fingerprint": fingerprint,
        "labels": {"alertname": alertname, **labels},
        "annotations": {"summary": f"{alertname} summary"},
        "startsAt": "2026-06-13T00:00:00Z",
    }


class FakeClient:
    def __init__(self):
        self.issues = {}  # number -> {"title","body","state"}
        self.comments = []  # (number, body)
        self._seq = 0

    def find_open_issue(self, marker):
        for number, issue in self.issues.items():
            if issue["state"] == "open" and marker in issue["body"]:
                return {"number": number, "body": issue["body"]}
        return None

    def create_issue(self, title, body, label_ids):
        self._seq += 1
        self.issues[self._seq] = {"title": title, "body": body, "state": "open"}
        return {"number": self._seq}

    def edit_issue(self, number, body=None, state=None):
        if body is not None:
            self.issues[number]["body"] = body
        if state is not None:
            self.issues[number]["state"] = state

    def comment(self, number, body):
        self.comments.append((number, body))

    def ensure_label_ids(self, names):
        return [1]


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.bridge = Bridge(self.client)

    def test_firing_creates_issue_with_marker(self):
        action = self.bridge.handle(_payload(alerts=[_alert()]))
        self.assertEqual(action, "created")
        issue = self.client.issues[1]
        self.assertIn(group_marker(group_digest('{}:{alertname="X"}')), issue["body"])
        self.assertEqual(issue["title"], "[alert] alertname=X")
        self.assertEqual(issue["state"], "open")

    def test_repeat_notification_is_absorbed(self):
        payload = _payload(alerts=[_alert()])
        self.bridge.handle(payload)
        action = self.bridge.handle(payload)
        self.assertEqual(action, "unchanged")
        self.assertEqual(len(self.client.issues), 1)
        self.assertEqual(self.client.comments, [])

    def test_changed_alert_set_updates_body_and_comments(self):
        self.bridge.handle(_payload(alerts=[_alert(fingerprint="fp1")]))
        action = self.bridge.handle(
            _payload(alerts=[_alert(fingerprint="fp1"), _alert(fingerprint="fp2")])
        )
        self.assertEqual(action, "updated")
        self.assertEqual(len(self.client.issues), 1)
        self.assertEqual(parse_fps(self.client.issues[1]["body"]), ["fp1", "fp2"])
        self.assertEqual(len(self.client.comments), 1)

    def test_resolved_closes_issue(self):
        self.bridge.handle(_payload(alerts=[_alert()]))
        action = self.bridge.handle(
            _payload(status="resolved", alerts=[_alert(status="resolved")])
        )
        self.assertEqual(action, "closed")
        self.assertEqual(self.client.issues[1]["state"], "closed")
        self.assertEqual(len(self.client.comments), 1)

    def test_resolved_without_issue_is_ignored(self):
        action = self.bridge.handle(
            _payload(status="resolved", alerts=[_alert(status="resolved")])
        )
        self.assertEqual(action, "ignored")
        self.assertEqual(self.client.issues, {})

    def test_refire_after_close_creates_new_issue(self):
        self.bridge.handle(_payload(alerts=[_alert()]))
        self.bridge.handle(_payload(status="resolved", alerts=[_alert(status="resolved")]))
        action = self.bridge.handle(_payload(alerts=[_alert()]))
        self.assertEqual(action, "created")
        self.assertEqual(len(self.client.issues), 2)

    def test_firing_status_with_empty_firing_list_treated_as_resolve(self):
        self.bridge.handle(_payload(alerts=[_alert()]))
        action = self.bridge.handle(
            _payload(status="firing", alerts=[_alert(status="resolved")])
        )
        self.assertEqual(action, "closed")

    def test_groups_are_independent(self):
        self.bridge.handle(_payload(alerts=[_alert()], group_key="k1"))
        self.bridge.handle(_payload(alerts=[_alert()], group_key="k2"))
        self.assertEqual(len(self.client.issues), 2)


class TestHelpers(unittest.TestCase):
    def test_build_title_sorts_labels(self):
        title = build_title({"namespace": "velero", "alertname": "KubeJobFailed"})
        self.assertEqual(title, "[alert] alertname=KubeJobFailed, namespace=velero")

    def test_build_title_without_labels(self):
        self.assertEqual(build_title({}), "[alert] (グループラベル無し)")

    def test_parse_fps_missing_marker(self):
        self.assertEqual(parse_fps("ただの本文"), [])


if __name__ == "__main__":
    unittest.main()
