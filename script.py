#!/usr/bin/env python3
"""
Script para inspecionar o banco de dados database.db
e ver todos os documentos e códigos de autenticação
"""

import sqlite3
from datetime import datetime

def conectar_db():
    """Conecta ao banco de dados"""
    try:
        conn = sqlite3.connect('database.db')
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"❌ Erro ao conectar: {e}")
        return None

def listar_tabelas(conn):
    """Lista todas as tabelas do banco"""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tabelas = cursor.fetchall()
    
    print("\n" + "="*80)
    print("📋 TABELAS NO BANCO DE DADOS")
    print("="*80)
    
    for tabela in tabelas:
        nome = tabela['name']
        cursor.execute(f"SELECT COUNT(*) as total FROM {nome}")
        total = cursor.fetchone()['total']
        print(f"📁 {nome}: {total} registros")
    
    return [t['name'] for t in tabelas]

def inspecionar_documentos_autenticados(conn):
    """Inspeciona a tabela de documentos autenticados"""
    print("\n" + "="*80)
    print("🔐 DOCUMENTOS AUTENTICADOS")
    print("="*80)
    
    cursor = conn.cursor()
    
    # Verificar se a tabela existe
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documentos_autenticados'")
    if not cursor.fetchone():
        print("❌ Tabela 'documentos_autenticados' NÃO EXISTE!")
        return
    
    # Mostrar estrutura da tabela
    cursor.execute("PRAGMA table_info(documentos_autenticados)")
    colunas = cursor.fetchall()
    print("\n📌 Colunas da tabela:")
    for col in colunas:
        print(f"   - {col['name']} ({col['type']})")
    
    # Buscar todos os documentos
    cursor.execute("""
        SELECT id, codigo, aluno_nome, aluno_ra, tipo, data_geracao, disciplina_id
        FROM documentos_autenticados 
        ORDER BY id DESC
    """)
    
    documentos = cursor.fetchall()
    
    print(f"\n📄 Total de documentos: {len(documentos)}")
    print("-" * 80)
    
    for doc in documentos:
        doc_dict = dict(doc)
        print(f"\n🔹 ID: {doc_dict['id']}")
        print(f"   Código: {doc_dict['codigo']}")
        print(f"   Aluno: {doc_dict['aluno_nome']} (RA: {doc_dict['aluno_ra']})")
        print(f"   Tipo: {doc_dict['tipo']}")
        print(f"   Data: {doc_dict['data_geracao']}")
        if doc_dict['disciplina_id']:
            print(f"   Disciplina ID: {doc_dict['disciplina_id']}")
        
        # Verificar se o código começa com DECL ou HIST
        if doc_dict['codigo']:
            if doc_dict['codigo'].startswith('DECL'):
                print(f"   🔍 TIPO: DECLARAÇÃO DE CONCLUSÃO")
            elif doc_dict['codigo'].startswith('HIST'):
                print(f"   🔍 TIPO: HISTÓRICO ESCOLAR")
            else:
                print(f"   🔍 TIPO: OUTRO ({doc_dict['codigo'][:10]}...)")

def inspecionar_documentos_enviados(conn):
    """Inspeciona a tabela de documentos enviados aos alunos"""
    print("\n" + "="*80)
    print("📨 DOCUMENTOS ENVIADOS AOS ALUNOS")
    print("="*80)
    
    cursor = conn.cursor()
    
    # Verificar se a tabela existe
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documentos_enviados'")
    if not cursor.fetchone():
        print("❌ Tabela 'documentos_enviados' NÃO EXISTE!")
        return
    
    cursor.execute("""
        SELECT de.id, de.codigo, de.tipo, de.titulo, de.aluno_id, de.data_envio, de.status,
               a.nome as aluno_nome
        FROM documentos_enviados de
        LEFT JOIN alunos a ON de.aluno_id = a.id
        ORDER BY de.id DESC
    """)
    
    enviados = cursor.fetchall()
    
    print(f"\n📄 Total de envios: {len(enviados)}")
    print("-" * 80)
    
    for env in enviados:
        env_dict = dict(env)
        print(f"\n📧 Envio ID: {env_dict['id']}")
        print(f"   Código: {env_dict['codigo']}")
        print(f"   Aluno: {env_dict['aluno_nome']} (ID: {env_dict['aluno_id']})")
        print(f"   Tipo: {env_dict['tipo']}")
        print(f"   Título: {env_dict['titulo']}")
        print(f"   Data: {env_dict['data_envio']}")
        print(f"   Status: {env_dict['status']}")

def buscar_por_codigo_especifico(conn, codigo):
    """Busca um código específico em todas as tabelas"""
    print("\n" + "="*80)
    print(f"🔍 BUSCANDO CÓDIGO: {codigo}")
    print("="*80)
    
    cursor = conn.cursor()
    
    # Buscar em documentos_autenticados
    try:
        cursor.execute("""
            SELECT * FROM documentos_autenticados 
            WHERE codigo = ? OR codigo LIKE ? OR codigo LIKE ?
        """, (codigo, f"%{codigo}%", f"{codigo}%"))
        
        resultados = cursor.fetchall()
        
        if resultados:
            print("\n✅ ENCONTRADO em documentos_autenticados:")
            for r in resultados:
                r_dict = dict(r)
                print(f"   ID: {r_dict.get('id')}")
                print(f"   Código: {r_dict.get('codigo')}")
                print(f"   Tipo: {r_dict.get('tipo')}")
                print(f"   Aluno: {r_dict.get('aluno_nome')}")
        else:
            print("\n❌ Não encontrado em documentos_autenticados")
    except Exception as e:
        print(f"Erro ao buscar: {e}")
    
    # Buscar em documentos_enviados
    try:
        cursor.execute("""
            SELECT * FROM documentos_enviados 
            WHERE codigo = ? OR codigo LIKE ? OR codigo LIKE ?
        """, (codigo, f"%{codigo}%", f"{codigo}%"))
        
        resultados = cursor.fetchall()
        
        if resultados:
            print("\n✅ ENCONTRADO em documentos_enviados:")
            for r in resultados:
                r_dict = dict(r)
                print(f"   ID: {r_dict.get('id')}")
                print(f"   Código: {r_dict.get('codigo')}")
                print(f"   Tipo: {r_dict.get('tipo')}")
                print(f"   Status: {r_dict.get('status')}")
    except Exception as e:
        pass

def verificar_disciplinas(conn):
    """Verifica as disciplinas cadastradas"""
    print("\n" + "="*80)
    print("📚 DISCIPLINAS")
    print("="*80)
    
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM disciplinas ORDER BY id")
    disciplinas = cursor.fetchall()
    
    for disc in disciplinas:
        print(f"   ID {disc['id']}: {disc['nome']}")

def main():
    print("\n🚀 INICIANDO INSPEÇÃO DO BANCO DE DADOS")
    print("="*80)
    
    conn = conectar_db()
    if not conn:
        return
    
    # Listar todas as tabelas
    tabelas = listar_tabelas(conn)
    
    # Inspecionar documentos
    inspecionar_documentos_autenticados(conn)
    inspecionar_documentos_enviados(conn)
    
    # Verificar disciplinas
    verificar_disciplinas(conn)
    
    # Se tiver um código específico para buscar (opcional)
    print("\n" + "="*80)
    print("🔎 BUSCAR CÓDIGO ESPECÍFICO")
    print("="*80)
    
    # Pegue um código do histórico que funciona
    # e um código da declaração que NÃO funciona
    print("\nDigite os códigos que você quer verificar (ou Enter para pular):")
    
    codigo_hist = input("Código do HISTÓRICO (que funciona): ").strip()
    if codigo_hist:
        buscar_por_codigo_especifico(conn, codigo_hist)
    
    codigo_decl = input("\nCódigo da DECLARAÇÃO (que NÃO funciona): ").strip()
    if codigo_decl:
        buscar_por_codigo_especifico(conn, codigo_decl)
    
    # Estatísticas gerais
    print("\n" + "="*80)
    print("📊 ESTATÍSTICAS GERAIS")
    print("="*80)
    
    cursor = conn.cursor()
    
    # Total de alunos
    cursor.execute("SELECT COUNT(*) as total FROM alunos")
    print(f"👥 Alunos: {cursor.fetchone()['total']}")
    
    # Total de declarações
    cursor.execute("SELECT COUNT(*) as total FROM documentos_autenticados WHERE tipo = 'declaracao_conclusao'")
    print(f"📜 Declarações de Conclusão: {cursor.fetchone()['total']}")
    
    # Total de históricos
    cursor.execute("SELECT COUNT(*) as total FROM documentos_autenticados WHERE tipo = 'historico'")
    print(f"📋 Históricos: {cursor.fetchone()['total']}")
    
    # Total de outros documentos
    cursor.execute("SELECT COUNT(*) as total FROM documentos_autenticados WHERE tipo NOT IN ('historico', 'declaracao_conclusao')")
    print(f"📄 Outros documentos: {cursor.fetchone()['total']}")
    
    conn.close()
    
    print("\n" + "="*80)
    print("✅ INSPEÇÃO CONCLUÍDA")
    print("="*80)

if __name__ == "__main__":
    main()