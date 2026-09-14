"""Document truth, transactions and UTF-8 offsets against isolated real MySQL 8."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import event

from app.modules.knowledge import public as knowledge
from tests.mysql_support import mysql_sandbox


@pytest.fixture
def database():
    with mysql_sandbox() as sandbox:
        service = knowledge.Knowledge(sandbox.settings())
        service.initialize_database()
        try:
            yield service, sandbox
        finally:
            service.close()


def whole(content):
    return (knowledge.ChunkWrite(0, content, 0, len(content.encode()), '', 2),)


def sql(sandbox, statement, parameters=()):
    with sandbox.connect() as connection, connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def test_create_list_and_detail_are_database_truth(database):
    service, sandbox = database
    first = service.create('one.md', b'# One\nBody')
    second = service.create('two.md', b'# Two\nBody')
    assert first.id > 0 and second.id > first.id
    assert first.source_type == 'UPLOAD' and first.index_status == 'PENDING'
    assert first.content == '# One\nBody' and first.chunk_count == 0
    assert first.tags == () and service.get(first.id) == first
    sql(sandbox, 'UPDATE document SET chunk_count=99 WHERE id=%s', (first.id,))
    assert service.get(first.id).chunk_count == 0
    page = service.list(page=0, size=1)
    assert page.total == 2 and page.items[0].id == second.id
    assert service.list(page=1, size=1).items[0].id == first.id
    assert service.list(page=2, size=1).items == ()


def test_title_search_uses_literal_substring_and_status_is_case_insensitive(database):
    service, _ = database
    wanted = service.create('note.md', b'# 100%_done\nBody')
    service.create('other.md', b'# 100percent_done\nBody')
    result = service.list(q=' %_ ', status='pending')
    assert result.total == 1 and result.items[0].id == wanted.id
    assert service.list(q='\u3000').total == 2


def test_list_does_not_fetch_document_bodies(database):
    service, _ = database
    service.create('one.md', b'# One\nBody')
    queries = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.startswith('SELECT document.id'):
            queries.append(statement)
    engine = service._database._engine  # Module-owned integration check of the real SQL projection.
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        assert service.list().total == 1
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert len(queries) == 1
    assert 'document.content' not in queries[0] and 'document.source_uri' not in queries[0]


@pytest.mark.parametrize('arguments', [{'page': -1}, {'size': 0}, {'size': 101}, {'status': 'UNKNOWN'}, {'status': ' pending '}])
def test_invalid_page_arguments_are_rejected(database, arguments):
    service, _ = database
    with pytest.raises(knowledge.InputRejected):
        service.list(**arguments)


CONTRACTS = json.loads(Path(__file__).with_name('contracts').joinpath('utf8-offsets.json').read_text(encoding='utf-8-sig'))['cases']


@pytest.mark.parametrize('case', CONTRACTS, ids=[case['name'] for case in CONTRACTS])
def test_chunk_state_and_source_contract_preserves_utf8_bytes(database, case):
    service, sandbox = database
    document = service.create('contract.md', b'placeholder')
    content = case['text']
    service.update(document.id, content)  # update retains a leading BOM as the Java contract requires
    sql(sandbox, "UPDATE document SET updated_at='2000-01-02 03:04:05' WHERE id=%s", (document.id,))
    drafts = tuple(knowledge.ChunkWrite(token_count=2, **entry) for entry in case['chunks'])
    chunks = service.begin_indexing(document.id, drafts, expected_content=content)
    assert service.get(document.id).index_status == 'INDEXING'
    assert service.get(document.id).chunk_count == len(drafts)
    assert service.chunks(document.id) == chunks
    service.mark_indexed(document.id)
    indexed = service.get(document.id)
    assert indexed.index_status == 'INDEXED' and indexed.index_error is None
    assert indexed.indexed_at is not None and indexed.updated_at == datetime(2000, 1, 2, 3, 4, 5)
    sources = service.sources(tuple(chunk.id for chunk in reversed(chunks)))
    assert [source.chunk_id for source in sources] == [chunk.id for chunk in chunks]
    for draft, saved, source in zip(drafts, chunks, sources):
        assert content.encode()[saved.byte_start:saved.byte_end].decode() == draft.text == source.text
        assert source.document_id == document.id
    assert sql(sandbox, 'SELECT char_start,char_end FROM chunk WHERE document_id=%s ORDER BY seq', (document.id,)) == tuple((c.byte_start, c.byte_end) for c in chunks)


def test_same_hash_update_preserves_bytes_metadata_chunks_and_state(database):
    service, sandbox = database
    document = service.create('a.md', b'---\ntitle: Original\ntags: [keep]\n---\n# H\nText')
    chunks = service.begin_indexing(document.id, whole(document.content))
    service.mark_indexed(document.id)
    sql(sandbox, "UPDATE document SET updated_at='2000-01-01' WHERE id=%s", (document.id,))
    result = service.update(document.id, '\t' + document.content.replace('\n', '\r\n') + ' \n')
    assert not result.changed
    assert result.document.content == document.content
    assert result.document.title == 'Original' and result.document.tags == ('keep',)
    assert result.document.index_status == 'INDEXED'
    assert result.document.updated_at > datetime(2000, 1, 1)
    assert service.chunks(document.id) == chunks


def test_changed_update_reindex_and_delete_have_distinct_timestamp_rules(database):
    service, sandbox = database
    document = service.create('fallback.md', b'# Original\nBody')
    service.begin_indexing(document.id, whole(document.content))
    service.mark_failed(document.id, 'known failure')
    result = service.update(document.id, 'Plain updated text')
    assert result.changed and result.document.title == 'fallback'
    assert result.document.index_status == 'PENDING' and result.document.index_error is None
    assert service.chunks(document.id) == ()
    old_chunks = service.begin_indexing(document.id, whole(result.document.content))
    service.mark_indexed(document.id)
    sql(sandbox, "UPDATE document SET updated_at='2000-01-01' WHERE id=%s", (document.id,))
    pending = service.prepare_reindex(document.id)
    assert pending.updated_at == datetime(2000, 1, 1) and pending.index_status == 'PENDING'
    assert service.chunks(document.id) == ()
    service.delete(document.id)
    assert service.list().total == 0 and service.sources(tuple(c.id for c in old_chunks)) == ()
    assert sql(sandbox, 'SELECT updated_at,chunk_count,index_status FROM document WHERE id=%s', (document.id,)) == ((datetime(2000, 1, 1), 0, 'PENDING'),)
    for method in (service.get, service.chunks, service.prepare_reindex, service.delete):
        with pytest.raises(knowledge.DocumentNotFound):
            method(document.id)


@pytest.mark.parametrize('operation', ['update','prepare_reindex','delete'])
def test_orphan_indexing_cannot_be_mutated(database, operation):
    service, _ = database
    document = service.create('a.md', b'Original')
    chunks = service.begin_indexing(document.id, whole(document.content))
    with pytest.raises(knowledge.IndexStateConflict):
        getattr(service, operation)(document.id, *(['Changed'] if operation == 'update' else []))
    assert service.get(document.id).content == document.content and service.chunks(document.id) == chunks


@pytest.mark.parametrize('drafts', [
    (), (knowledge.ChunkWrite(1,'hello',0,5,'',1),),
    (knowledge.ChunkWrite(0,'wrong',0,5,'',1),),
    (knowledge.ChunkWrite(0,'hell',0,4,'',1),),
    (knowledge.ChunkWrite(0,'hello',0,5,'',-1),),
    (knowledge.ChunkWrite(0,'hello',0,5,'x'*513,1),),
], ids=['empty','sequence','wrong-text','incomplete','negative-tokens','heading-too-long'])
def test_invalid_chunks_cannot_advance_state_or_change_existing_rows(database, drafts):
    service, _ = database
    document = service.create('a.md', b'hello')
    with pytest.raises(knowledge.InputRejected):
        service.begin_indexing(document.id, drafts)
    assert service.get(document.id).index_status == 'PENDING' and service.chunks(document.id) == ()


def test_content_changed_since_split_is_rejected(database):
    service, _ = database
    document = service.create('a.md', b'Original')
    service.update(document.id, 'Changed')
    with pytest.raises(knowledge.IndexStateConflict):
        service.begin_indexing(document.id, whole(document.content), expected_content=document.content)
    assert service.get(document.id).index_status == 'PENDING'


def test_chunk_replacement_rolls_back_state_rows_and_count_together(database):
    service, sandbox = database
    document = service.create('a.md', b'hello world')
    previous = service.begin_indexing(document.id, whole(document.content))
    service.mark_failed(document.id, 'previous failure')
    sql(sandbox, "UPDATE document SET index_status='PENDING' WHERE id=%s", (document.id,))
    sql(sandbox, "CREATE TRIGGER reject_second BEFORE INSERT ON chunk FOR EACH ROW BEGIN IF NEW.seq=1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test write failure'; END IF; END")
    drafts = (knowledge.ChunkWrite(0,'hello ',0,6,'',1), knowledge.ChunkWrite(1,'world',6,11,'',1))
    with pytest.raises(knowledge.DatabaseUnavailable):
        service.begin_indexing(document.id, drafts)
    assert service.chunks(document.id) == previous
    current = service.get(document.id)
    assert current.index_status == 'PENDING' and current.index_error == 'previous failure' and current.chunk_count == 1


def test_concurrent_claims_only_one_can_index_a_document(database):
    service, _ = database
    document = service.create('a.md', b'hello')
    def attempt():
        try:
            return service.begin_indexing(document.id, whole(document.content))
        except knowledge.IndexStateConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(service.chunks(document.id)) == 1


@pytest.mark.parametrize('error', ['', '\u3000', '\ud800', 'x'*1025], ids=['empty','blank','surrogate','overlong'])
def test_failed_state_requires_valid_diagnostic(database, error):
    service, _ = database
    document = service.create('a.md', b'hello')
    with pytest.raises(knowledge.InputRejected):
        service.mark_failed(document.id, error)
    assert service.get(document.id).index_status == 'PENDING'


def test_terminal_transitions_cannot_be_repeated(database):
    service, _ = database
    document = service.create('a.md', b'hello')
    with pytest.raises(knowledge.IndexStateConflict):
        service.mark_indexed(document.id)
    service.mark_failed(document.id, 'failed before splitting')
    with pytest.raises(knowledge.IndexStateConflict):
        service.mark_failed(document.id, 'again')
    assert service.pending_ids() == ()


def test_business_writes_cannot_bypass_explicit_schema_adoption():
    with mysql_sandbox() as sandbox:
        from tests.mysql_schema_integration import install_legacy
        install_legacy(sandbox)
        service = knowledge.Knowledge(sandbox.settings())
        try:
            with pytest.raises(knowledge.SchemaMismatch):
                service.create('a.md', b'hello')
            assert sql(sandbox, 'SELECT COUNT(*) FROM document') == ((0,),)
        finally:
            service.close()
