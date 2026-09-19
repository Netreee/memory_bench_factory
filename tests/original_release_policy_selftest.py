"""Actual helper and release tests; scripted opinions, no semantic quality claim."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from pipeline import grounding_review as production, quality
from pipeline import process_proposals, question_contract, question_wording
from pipeline.corpus_contract import review_documents, attach_receipts, fidelity_requirements, canonical_context
from pipeline.semantic_review import review_questions
from eval.provenance import public_protocol
from eval.answer_task_review import POLICY_VERSION, append_scoring_policy
from eval import semantic_judge
from process_proposals_selftest import fixture as process_fixture, Fake
from corpus_fixture_helpers import fixed_positive_review
from structured_reference_workflow_selftest import StructuredScript
from original_process_downstream_selftest import question, CORPUS, PROTOCOL

candidate = production


def actual_review(q, corpus=CORPUS, protocol=PROTOCOL, isolated=True, unsupported=False):
    script = StructuredScript()
    def call(step, messages, **kwargs):
        value = script(step, messages, **kwargs)
        if unsupported and step.endswith('adjudicate'):
            value['reference_status'] = 'unsupported'
        return value
    return review_questions([q], corpus, protocol, chat_json=call,
        reviewer_model='offline', reference_auditor_model='offline' if isolated else None, max_calls=3)


def release_fixture(directory, *, unsupported=False):
    wp, ws, proposal = process_fixture()
    report = process_proposals.propose_process_orders(wp, ws, target=1, model='offline',
        chat_json=Fake({'proposals': [proposal], 'reason': 'Only a wiring fixture.'}))
    order = report['orders'][0]
    q = question_contract.attach_question_contract(question_contract.bind_question_world(order, ws), wp)
    q['question'] = proposal['intent']
    q['question_validation'] = {'semantic_review': question_wording.review_wording(
        q['question'], q['question_contract'], model='offline', chat_json=Fake({
            'verdict': 'equivalent', 'reason': 'Only a wiring fixture.', 'issues': []}))}
    class Reviewer:
        def chat_json(self, *args, **kwargs):
            return fixed_positive_review(args[1])
    corpus = {'sessions': []}
    for sid in range(ws.n_sessions):
        content = ('Doc was received. Its state is received.' if sid == 0 else
            'Analysis adopted the received Doc, uses Doc, and its state is pending.')
        docs = [{'doc_id': f'doc{sid}', 'title': '', 'content': content, 'is_filler': False}]
        receipt = review_documents(Reviewer(), ws, sid, docs, requirements=fidelity_requirements(ws, sid))
        assert receipt['status'] == 'passed', receipt
        attach_receipts(docs, receipt, sid)
        corpus['sessions'].append({'session_id': sid, 'date': canonical_context(ws, sid)['document_date'], 'docs': docs})
    from pipeline.factory import ANSWER_PROTOCOL
    about = {'answer_protocol': ANSWER_PROTOCOL}
    protocol = public_protocol(about)
    candidates = candidate.candidates_with_evidence([q], corpus, isolated_reference=True)
    review = actual_review(candidates[0], corpus, protocol, unsupported=unsupported)
    final, selection = candidate.selection(candidates, review)
    # A negative test deliberately attempts to publish the rejected candidate.
    artifacts = {'01_whitepaper.json': wp, '02_world.json': ws.to_dict(),
        '04_questions.json': [q], '05_corpus.json': {'corpus': corpus},
        '06_grounded_questions.json': candidates if unsupported else final,
        '06_semantic_review.json': review, '00_about.json': about,
        'manifest.json': {'algo': {}, 'config': {}}}
    for name, value in artifacts.items():
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return artifacts


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network prohibited'))
        cls.net.start()
    @classmethod
    def tearDownClass(cls):
        cls.net.stop()

    def test_actual_helper_accepts_current_process_review(self):
        q = question(); report = actual_review(q)
        candidate.validate_current_review([q], CORPUS, PROTOCOL, report)

    def test_legacy_protocol_does_not_acquire_A(self):
        q = {'qid': 'old', 'question': 'What is recorded?', 'reference_proposal': {
            'answer': {'state': 'pending'}, 'rationale': 'Offline fixture.'}}
        protocol = 'Only supplied public material.'
        report = actual_review(q, protocol=protocol)
        with patch.object(semantic_judge, 'SemanticJudge', wraps=semantic_judge.SemanticJudge) as ctor:
            candidate.validate_current_review([q], CORPUS, protocol, report)
        self.assertIsNone(ctor.call_args.kwargs['scoring_policy'])

    def test_removed_or_malformed_A_cannot_be_invented(self):
        q = question()
        for protocol in ('Only supplied public material.', PROTOCOL.replace('主要', '被篡改', 1)):
            with self.subTest(protocol=protocol):
                if protocol == PROTOCOL:
                    self.fail('Malformed policy mutation ineffective')
                report = actual_review(q, protocol=protocol)
                with self.assertRaises(ValueError):
                    candidate.validate_current_review([q], CORPUS, protocol, report)

    def test_nonisolated_reference_is_not_made_eligible(self):
        q = question(); report = actual_review(q, isolated=False)
        with self.assertRaisesRegex(ValueError, 'Process questions require'):
            candidate.validate_current_review([q], CORPUS, PROTOCOL, report)

    def test_changed_reference_or_corpus_still_rejected(self):
        q = question(); report = actual_review(q)
        changed = deepcopy(q); changed['reference_proposal']['answer'] = {'changed': True}
        corpus = deepcopy(CORPUS); corpus['sessions'][0]['docs'][0]['content'] += ' New content.'
        for questions, docs in [([changed], CORPUS), ([q], corpus)]:
            with self.subTest(changed=questions == [changed]), self.assertRaises(ValueError):
                candidate.validate_current_review(questions, docs, PROTOCOL, report)

    def test_actual_quality_accepts_current_process_review_no_gate_mock(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td); release_fixture(directory)
            with patch.object(production, 'validate_current_review', candidate.validate_current_review):
                after = quality.evaluate_release(directory)
            self.assertTrue(after['eligible'], after['issues'])

    def test_actual_quality_rejects_stale_or_removed_protocol(self):
        for mutation in ('reference', 'protocol', 'report'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                directory = Path(td); artifacts = release_fixture(directory)
                if mutation == 'reference':
                    name = '06_grounded_questions.json'
                    artifacts[name][0]['reference_proposal']['answer'] = {'state': 'different'}
                elif mutation == 'protocol':
                    name = '00_about.json'; artifacts[name] = {'answer_protocol': {'version': 0}}
                else:
                    name = '06_semantic_review.json'; artifacts[name]['items'][0]['reference_status'] = 'unsupported'
                (directory / name).write_text(json.dumps(artifacts[name], ensure_ascii=False), encoding='utf-8')
                with patch.object(production, 'validate_current_review', candidate.validate_current_review):
                    report = quality.evaluate_release(directory)
                self.assertFalse(report['eligible'], report)

    def test_actual_quality_does_not_release_semantic_unsupported(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td); release_fixture(directory, unsupported=True)
            with patch.object(production, 'validate_current_review', candidate.validate_current_review):
                report = quality.evaluate_release(directory)
            self.assertFalse(report['eligible'])
            self.assertIn('semantic_selection_mismatch', {x['code'] for x in report['issues']})


if __name__ == '__main__':
    unittest.main()
