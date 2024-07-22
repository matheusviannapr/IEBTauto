import numpy as np
import pandas as pd
import ezdxf
import streamlit as st
from io import BytesIO
from ezdxf.enums import TextEntityAlignment
from pylatex import Document, Section, Command, Package, Subsection
from pylatex.utils import NoEscape
import requests
# Funções para cálculos elétricos
def calcular_corrente_nominal(potencia, tensao, fator_potencia, num_fases):
    if num_fases == 1:
        return potencia * fator_potencia / (tensao)
    elif num_fases == 2:
        return potencia * fator_potencia / (np.sqrt(3) * tensao)
    elif num_fases == 3:
        return potencia * fator_potencia / (3 * tensao)
    else:
        raise ValueError("Número de fases inválido.")

def encontrar_fator_correcao(temperatura, tabela_temperatura):
    coluna_temperatura = tabela_temperatura.columns[0]
    coluna_fator = tabela_temperatura.columns[1]
    fatores = tabela_temperatura[tabela_temperatura[coluna_temperatura] <= temperatura]
    if not fatores.empty:
        return fatores.iloc[-1][coluna_fator]
    else:
        raise ValueError("Temperatura fora do alcance da tabela.")

def encontrar_fator_agrupamento(num_circuitos, tabela_agrupamento):
    fatores = tabela_agrupamento[tabela_agrupamento['Agrupamento de circuitos'] <= num_circuitos]
    if not fatores.empty:
        return fatores.iloc[-1]['FatordeAgrupamento']
    else:
        raise ValueError("Número de circuitos fora do alcance da tabela.")

def determinar_secao_condutor(corrente, tabela_capacidade, metodo_instalacao):
    coluna_capacidade = [col for col in tabela_capacidade.columns if metodo_instalacao in col][0]
    secoes_suportadas = tabela_capacidade[tabela_capacidade[coluna_capacidade] >= corrente]
    if not secoes_suportadas.empty:
        return secoes_suportadas.iloc[0]['Seção do condutor']
    else:
        raise ValueError("Corrente muito alta para as seções de condutores disponíveis.")

def encontrar_capacidade_corrente(secao_condutor, tabela_capacidade, metodo_instalacao):
    colunas_validas = [col for col in tabela_capacidade.columns if metodo_instalacao in col]
    if not colunas_validas:
        raise ValueError(f"O método de instalação '{metodo_instalacao}' não foi encontrado na tabela de capacidade.")
    coluna_capacidade = colunas_validas[0]
    capacidade = tabela_capacidade.loc[tabela_capacidade['Seção do condutor'] == secao_condutor, coluna_capacidade].iloc[0]
    return capacidade

def determinar_disjuntor(corrente_corrigida, secao_final, tabela_disjuntores, tabela_capacidade, metodo_instalacao, numero_fases):
    capacidade_condutor = encontrar_capacidade_corrente(secao_final, tabela_capacidade, metodo_instalacao)
    
    # Definindo o tipo de disjuntor baseado no número de fases
    if numero_fases == 1:
        tipo_disjuntor = 'Monopolar'
    elif numero_fases == 2:
        tipo_disjuntor = 'Bipolar'
    elif numero_fases == 3:
        tipo_disjuntor = 'Tripolar'
    else:
        raise ValueError("Número de fases inválido. Deve ser 1, 2 ou 3.")
    
    # Filtrando a tabela de disjuntores de acordo com o tipo
    tabela_disjuntores_filtrada = tabela_disjuntores[tabela_disjuntores['Tipo de disjuntor'] == tipo_disjuntor]
    
    # Ordenando a tabela filtrada pela corrente nominal
    tabela_disjuntores_ordenada = tabela_disjuntores_filtrada.sort_values(by='Corrente nominal')
    
    # Iterando pela tabela ordenada para encontrar o disjuntor adequado
    for index, disjuntor in tabela_disjuntores_ordenada.iterrows():
        if corrente_corrigida < disjuntor['Corrente nominal'] < capacidade_condutor:
            return disjuntor['Corrente nominal']
    
    # Caso não encontre um disjuntor adequado
    return None

def calcular_queda_tensao(corrente_nominal, comprimento, secao_condutor, tabela_queda_tensao):
    valor_queda_tensao = tabela_queda_tensao.loc[tabela_queda_tensao['seção do condutor'] == secao_condutor, 'Queda de tensão (V/A.km)'].iloc[0]
    queda_tensao = valor_queda_tensao * corrente_nominal * comprimento
    return queda_tensao

def ajustar_secao_condutor_para_queda_tensao(secao_atual, tabela_capacidade):
    secoes_disponiveis = tabela_capacidade['Seção do condutor']
    secoes_maiores = secoes_disponiveis[secoes_disponiveis > secao_atual]
    if not secoes_maiores.empty:
        return secoes_maiores.iloc[0]
    else:
        raise ValueError("Não há seções de condutor maiores disponíveis.")

def ajustar_condutor_queda_tensao(corrente_nominal, comprimento, secao_inicial, queda_tensao_max_admitida, tabela_capacidade, tabela_queda_tensao):
    secao_condutor = secao_inicial
    queda_tensao = calcular_queda_tensao(corrente_nominal, comprimento, secao_condutor, tabela_queda_tensao)
    while queda_tensao > queda_tensao_max_admitida:
        secao_condutor = ajustar_secao_condutor_para_queda_tensao(secao_condutor, tabela_capacidade)
        queda_tensao = calcular_queda_tensao(corrente_nominal, comprimento, secao_condutor, tabela_queda_tensao)
    return secao_condutor, queda_tensao

def calcular_parametros_circuitos(lista_circuitos, data_tables):
    resultados = []
    for circuito in lista_circuitos:
        corrente_nominal = calcular_corrente_nominal(circuito['potencia'], circuito['tensao'], circuito['fator_potencia'], circuito['num_fases'])
        fator_correcao_temp = encontrar_fator_correcao(circuito['temperatura'], data_tables['Fator de correção de temperatur'])
        fator_agrupamento = encontrar_fator_agrupamento(circuito['num_circuitos'], data_tables['Fator de agrupamento'])
        corrente_corrigida = corrente_nominal / (fator_correcao_temp * fator_agrupamento)

        secao_inicial = determinar_secao_condutor(corrente_corrigida, data_tables['Capacidade de corrente'], circuito['met_instala'])
        secao_final, queda_tensao_final = ajustar_condutor_queda_tensao(corrente_nominal, circuito['comprimento'], secao_inicial, circuito['queda_tensao_max_admitida'], data_tables['Capacidade de corrente'], data_tables['queda de tensão'])
        disjuntor = determinar_disjuntor(corrente_corrigida, secao_final, data_tables['valores nominais de disjuntores'], data_tables['Capacidade de corrente'], circuito['met_instala'],circuito['num_fases'])

        resultados.append({
            "Nome do Circuito": circuito['nome'],
            "Seção do Condutor (mm²)": secao_final,
            "Disjuntor": disjuntor,
            "Queda de Tensão (Volts)": queda_tensao_final,
            "Corrente corrigida": corrente_corrigida,
            "Corrente Nominal": corrente_nominal,
            "Fator correção temperatura": fator_correcao_temp,
            "Fator Agrupamento": fator_agrupamento,
            "Número de fases" : circuito['num_fases'],
            "Comprimento": circuito['comprimento']

        })

        # Atualizando circuito com novos dados
        circuito.update({
            'Seção do Condutor (mm²)': secao_final,
            'Disjuntor (Ampere)': disjuntor,
            'Queda de Tensão (Volts)': queda_tensao_final,
            'Corrente corrigida': corrente_corrigida,
            'Corrente Nominal': corrente_nominal,
            'Fator correção temperatura': fator_correcao_temp,
            'Fator Agrupamento': fator_agrupamento
        })

    return pd.DataFrame(resultados), lista_circuitos

def calcular_disjuntor_geral(circuitos, tabela_fator_demanda, tensao_nominal):
    disjuntores_gerais = {}
    quadros = {}
    for circuito in circuitos:
        quadro = circuito['Quadro']
        if quadro not in quadros:
            quadros[quadro] = []
        quadros[quadro].append(circuito)
    for quadro, circuitos_quadro in quadros.items():
        num_circuitos_quadro = len(circuitos_quadro)
        fator_demanda_quadro = tabela_fator_demanda.get(num_circuitos_quadro, 1)
        potencia_total_quadro = sum(circuito['potencia'] for circuito in circuitos_quadro)
        corrente_total_quadro = calcular_corrente_nominal(potencia_total_quadro * fator_demanda_quadro, tensao_nominal, 0.9, 3)
        disjuntor_quadro = encontrar_disjuntor_menor(corrente_total_quadro, data_tables['valores nominais de disjuntores'])
        disjuntores_gerais[quadro] = disjuntor_quadro
    return disjuntores_gerais

def calcular_disjuntor_qgbt(disjuntores_gerais, tabela_fator_demanda_qgbt, tensao_nominal):
    corrente_total = sum(disjuntores_gerais.values())
    num_quadros = len(disjuntores_gerais)
    fator_demanda_qgbt = tabela_fator_demanda_qgbt.loc[tabela_fator_demanda_qgbt['num_circuitos'] == num_quadros, 'FatordeDemanda'].iloc[0]
    corrente_ajustada = corrente_total * fator_demanda_qgbt
    disjuntor_qgbt = corrente_ajustada
    return disjuntor_qgbt

def distribuir_fases(circuitos, fases_qd):
    carga_fase = {'R': 0, 'S': 0, 'T': 0}
    
    for circuito in circuitos:
        num_fases = circuito['num_fases']
        potencia = circuito['potencia']
        
        if fases_qd == 3:
            if num_fases == 1:
                fase = min(carga_fase, key=carga_fase.get)
                carga_fase[fase] += potencia
                circuito['Fases'] = fase
            elif num_fases == 2:
                fases = sorted(carga_fase, key=carga_fase.get)[:2]
                carga_fase[fases[0]] += potencia / 2
                carga_fase[fases[1]] += potencia / 2
                circuito['Fases'] = fases[0] + fases[1]
            elif num_fases == 3:
                carga_fase['R'] += potencia / 3
                carga_fase['S'] += potencia / 3
                carga_fase['T'] += potencia / 3
                circuito['Fases'] = 'RST'
        
        elif fases_qd == 2:
            if num_fases == 1:
                fase = min(['R', 'S'], key=lambda f: carga_fase[f])
                carga_fase[fase] += potencia
                circuito['Fases'] = fase
            elif num_fases == 2:
                carga_fase['R'] += potencia / 2
                carga_fase['S'] += potencia / 2
                circuito['Fases'] = 'RS'
        
        elif fases_qd == 1:
            if num_fases == 1:
                carga_fase['R'] += potencia
                circuito['Fases'] = 'R'
    
    return circuitos


def encontrar_disjuntor_menor(corrente, tabela_disjuntores):
    tabela_disjuntores_ordenada = tabela_disjuntores.sort_values(by='Corrente nominal', ascending=False)
    for index, disjuntor in tabela_disjuntores_ordenada.iterrows():
        if disjuntor['Corrente nominal'] < corrente:
            return disjuntor['Corrente nominal']
    return None

def ordenar_circuitos(circuitos):
    return sorted(circuitos, key=lambda x: (x['Quadro'], -x['num_fases'], -x['potencia']))

def criar_lista_materiais(circuitos, disjuntores_gerais):
    materiais = {}
    for circuito in circuitos:
        num_fases = circuito['num_fases']
        disjuntor = circuito['Disjuntor (Ampere)']
        if num_fases not in materiais:
            materiais[num_fases] = {}
        if disjuntor not in materiais[num_fases]:
            materiais[num_fases][disjuntor] = 0
        materiais[num_fases][disjuntor] += 1
    for quadro, disjuntor_quadro in disjuntores_gerais.items():
        num_fases = 3
        if num_fases not in materiais:
            materiais[num_fases] = {}
        if disjuntor_quadro not in materiais[num_fases]:
            materiais[num_fases][disjuntor_quadro] = 0
        materiais[num_fases][disjuntor_quadro] += 1
    return materiais

def ler_materiais_existentes(nome_arquivo):
    df = pd.read_excel(nome_arquivo)
    materiais_existentes = {}
    for _, row in df.iterrows():
        num_fases = row['num_fases']
        corrente = row['corrente']
        quantidade = row['Quantidade']
        if num_fases not in materiais_existentes:
            materiais_existentes[num_fases] = {}
        if corrente not in materiais_existentes[num_fases]:
            materiais_existentes[num_fases][corrente] = 0
        materiais_existentes[num_fases][corrente] += quantidade
    return materiais_existentes

def cruzar_listas_materiais(materiais_necessarios, materiais_existentes):
    materiais_compra = {}
    materiais_ociosos = {k: v.copy() for k, v in materiais_existentes.items()}
    for num_fases, disjuntores in materiais_necessarios.items():
        for corrente, quantidade_necessaria in disjuntores.items():
            if num_fases not in materiais_ociosos:
                materiais_ociosos[num_fases] = {}
            quantidade_existente = materiais_ociosos[num_fases].get(corrente, 0)
            quantidade_comprar = max(0, quantidade_necessaria - quantidade_existente)
            if quantidade_comprar > 0:
                if num_fases not in materiais_compra:
                    materiais_compra[num_fases] = {}
                materiais_compra[num_fases][corrente] = quantidade_comprar
            materiais_ociosos[num_fases][corrente] = max(0, quantidade_existente - quantidade_necessaria)
    for num_fases in materiais_existentes:
        for corrente in materiais_existentes[num_fases]:
            if num_fases not in materiais_necessarios or corrente not in materiais_necessarios[num_fases]:
                quantidade_existente = materiais_existentes[num_fases].get(corrente, 0)
                materiais_ociosos[num_fases][corrente] = max(materiais_ociosos[num_fases].get(corrente, 0), quantidade_existente)
    return materiais_compra, materiais_ociosos

# Função para ler os dados dos circuitos da planilha Excel
def ler_circuitos_de_excel(file_path):
    circuito_data = pd.read_excel(file_path)
    circuitos = circuito_data.to_dict(orient='records')
    return circuitos

# Função para carregar dados
def ler_dados(file_path):
    if file_path.name.endswith('.xls'):
        data_sheets = pd.read_excel(file_path, sheet_name=None, engine='xlrd')
    elif file_path.name.endswith('.xlsx'):
        data_sheets = pd.read_excel(file_path, sheet_name=None, engine='openpyxl')
    else:
        st.error('Tipo de arquivo não suportado. Por favor, carregue um arquivo .xls ou .xlsx.')
        return None
    data_tables = {sheet_name: data_sheets[sheet_name] for sheet_name in data_sheets}
    return data_tables

def formatar_tabela_latex(circuitos, disjuntores_gerais, disjuntor_qgbt):
    tabela_latex = "\\begin{landscape} \n"
    tabela_latex +="\\section{Memória de Cálculo dos Circuitos - Tabelas} \n"
    tabela_latex +="\\fontsize{5}{5}\selectfont \n"
    tabela_latex += "\\begin{tabular}{|l|l|l|l|l|l|l|l|l|l|l|l|l|}\n\\hline\n"
    tabela_latex += "Nome do Circuito & Potência (W) & Tensão (V) & FP & Nº de Fases & Temp (°C) & Nº de Circuitos & Comprimento (km) & Condutor(mm²) & Disjuntor(A) & delta (V) & Fases & Quadro \\\\ \\hline\n"
    circuitos_ordenados = sorted(circuitos, key=lambda x: x['nome'])
    for circuito in circuitos_ordenados:
        linha = f"{circuito['nome']} & {circuito['potencia']} & {circuito['tensao']} & {circuito['fator_potencia']} & {circuito['num_fases']} & {circuito['temperatura']} & {circuito['num_circuitos']} & {circuito['comprimento']} & {circuito['Seção do Condutor (mm²)']} & {circuito['Disjuntor (Ampere)']} & {round(circuito['Queda de Tensão (Volts)'],2)} & {circuito['Fases']} & {circuito['Quadro']} \\\\ \\hline\n"
        tabela_latex += linha
    tabela_latex += "\\end{tabular}\n\n"
    tabela_latex += "\\begin{tabular}{|l|l|}\n\\hline\n"
    tabela_latex += "Quadro & Disjuntor Geral (A) \\\\ \\hline\n"
    for quadro, disjuntor in disjuntores_gerais.items():
        linha_disjuntor = f"{quadro} & {disjuntor} \\\\ \\hline\n"
        tabela_latex += linha_disjuntor
    tabela_latex += "\\hline\n"
    tabela_latex += f"QGBT & {disjuntor_qgbt} \\\\ \\hline\n"
    tabela_latex += "\\end{tabular} \n"
    tabela_latex += "\\end{landscape}"
    return tabela_latex

def memcalc(circuitos, resultados_circuitos, tabela_queda_tensao):
    latex_content = "\\section{Memória de Cálculo dos Circuitos}\n\n"
    circuitos_ordenados = sorted(circuitos, key=lambda x: x['nome'])
    for circuito in circuitos_ordenados:
        nome = circuito['nome']
        potencia = circuito['potencia']
        tensao = circuito['tensao']
        fator_potencia = circuito['fator_potencia']
        num_fases = circuito['num_fases']
        secao_condutor = circuito['Seção do Condutor (mm²)']
        comprimento = circuito['comprimento']
        resultado = resultados_circuitos.loc[resultados_circuitos['Nome do Circuito'] == nome].iloc[0]
        corrente_nominal = resultado['Corrente Nominal']
        corrente_nominal = round(corrente_nominal, 2)
        fator_agrupamento = resultado['Fator Agrupamento']
        fator_correcao_temp = resultado['Fator correção temperatura']
        corrente_corrigida = resultado['Corrente corrigida']
        corrente_corrigida = round(corrente_corrigida, 2)
        valor_queda_tensao = tabela_queda_tensao.loc[tabela_queda_tensao['seção do condutor'] == secao_condutor, 'Queda de tensão (V/A.km)'].iloc[0]
        queda_tensao = valor_queda_tensao * corrente_nominal * comprimento
        queda_tensao = round(queda_tensao,2)
        n_factor = '0' if num_fases == 1 else '1' if num_fases == 2 else '2'
        latex_content += f"\\subsection*{{Circuito: {nome}}}\n"
        latex_content += "\\begin{itemize}\n"
        latex_content += f"    \\item \\textbf{{Dados do Circuito:}} Potência = {potencia}W, Tensão = {tensao}V, Fator de Potência = {fator_potencia}, Número de Fases = {num_fases}.\n"
        latex_content += f"    \\item \\textbf{{Cálculo da Corrente Nominal (Inominal):}} \[ I_{{\\text{{nominal}}}} = \\frac{{{potencia}}}{{\\sqrt{{3}}^{{{n_factor}}} \\times {tensao} \\times {fator_potencia}}} \] = {corrente_nominal} A.\n"
        latex_content += f"    \\item \\textbf{{Cálculo da Corrente Corrigida (Icorrigida):}} \n"
        latex_content += f"    \\[ I_{{\\text{{corrigida}}}} = \\frac{{I_{{\\text{{nominal}}}}}}{{\\text{{Fator Temperatura}} \\times \\text{{Fator Agrupamento}}}} = \\frac{{{corrente_nominal}}}{{{fator_correcao_temp} \\times {fator_agrupamento}}} \\] = {corrente_corrigida} A.\n"
        latex_content += "    \\item \\textbf{{Cálculo da Queda de Tensão:}}\n"
        latex_content += "    A queda de tensão é calculada pela fórmula: \n"
        latex_content += "    \\[ $\\Delta V = I_{\\text{nominal}} \\times \\text{Comprimento} \\times \\text{(V/A.km)}$ \\]\n"
        latex_content += f"    Onde para este circuito, \n"
        latex_content += f"    \\begin{{align*}}\n"
        latex_content += f"    I_{{\\text{{nominal}}}} &= {corrente_nominal} \\text{{ A}}, \\\\\n"
        latex_content += f"    \\text{{Comprimento}} &= {comprimento} \\text{{ km}}, \\\\\n"
        latex_content += f"    \\text{{Queda de Tensão (V/A.km)}} &= {valor_queda_tensao} \\text{{ V/A.km}}. \n"
        latex_content += f"    \\end{{align*}}\n"
        latex_content += f"    Portanto, a queda de tensão calculada é: \n"
        latex_content += f"    \\[ \\Delta V = {valor_queda_tensao} \\times {corrente_nominal} \\times {comprimento} = {queda_tensao} \\text{{ V}}. \\]\n"
        latex_content += "\\end{itemize}\n\n"
    return latex_content

def criar_relatorio_latex(circuitos, resultados, caminho_salvar, disjuntores_gerais, disjuntor_qgbt, data_tables):
    doc = Document(documentclass='article', document_options='11pt')
    
    # Adiciona os pacotes necessários
    doc.packages.append(Package('makeidx'))
    doc.packages.append(Package('multirow'))
    doc.packages.append(Package('multicol'))
    doc.packages.append(Package('xcolor', options='dvipsnames,svgnames,table'))
    doc.packages.append(Package('graphicx'))
    doc.packages.append(Package('epstopdf'))
    doc.packages.append(Package('ulem'))
    doc.packages.append(Package('hyperref'))
    doc.packages.append(Package('amsmath'))
    doc.packages.append(Package('lmodern'))
    doc.packages.append(Package('amssymb'))
    doc.packages.append(Package('pdflscape'))
    doc.packages.append(Package('geometry', options='paperwidth=595pt,paperheight=841pt,top=23pt,right=56pt,bottom=56pt,left=56pt'))
    
    # Adiciona o autor e título
    doc.preamble.append(Command('author', 'CHRISTINE CACERES BURGHART'))
    doc.preamble.append(Command('title', ''))
    
    # Adiciona o novo ambiente de indentação
    doc.preamble.append(NoEscape(r"""
    \makeatletter
    \newenvironment{indentation}[3]%
    {\par\setlength{\parindent}{#3}
    \setlength{\leftmargin}{#1}       \setlength{\rightmargin}{#2}%
    \advance\linewidth -\leftmargin       \advance\linewidth -\rightmargin%
    \advance\@totalleftmargin\leftmargin  \@setpar{{\@@par}}%
    \parshape 1\@totalleftmargin \linewidth\ignorespaces}{\par}%
    \makeatother
    """))
    
    # Adiciona o início do documento
    doc.append(NoEscape(r'\begin{document}'))
    doc.append(NoEscape(r'\begin{center}'))
    doc.append(NoEscape(r'\large \textbf{OBJETO:} Memorial de dimensionamento para os circuitos do NOMEDOPROJETO'))
    doc.append(NoEscape(r'\end{center}'))
    
    # Adiciona seções ao documento
    with doc.create(Section('Introdução')):
        doc.append('Este documento descreve o procedimento técnico detalhado para o dimensionamento de condutores e disjuntores em circuitos elétricos residenciais, baseando-se nas normas técnicas ABNT NBR 5410 e ABNT NBR 5471. A metodologia aborda a determinação da seção transversal dos condutores e a escolha de disjuntores, levando em consideração critérios como capacidade de condução de corrente, queda de tensão, e proteção contra sobrecarga e curto-circuito.')
    
    with doc.create(Section('Metodologia e Normas Aplicadas')):
        doc.append('O dimensionamento dos condutores elétricos segue as diretrizes estabelecidas pelas normas ABNT NBR 5410 e ABNT NBR 5471, que definem os padrões para instalações elétricas de baixa tensão e para condutores de energia elétrica, respectivamente.')
    
    with doc.create(Section('Cálculos e Resultados')):
        with doc.create(Subsection('Cálculo da Corrente Nominal do Circuito')):
            doc.append(NoEscape(r'A corrente nominal (\(I_{\text{nominal}}\)) é calculada pela fórmula:'))
            doc.append(NoEscape(r'\[ I_{\text{nominal}} = \frac{P}{\sqrt{3} \cdot V \cdot \cos(\phi)} \]'))
            doc.append(NoEscape(r'para circuitos trifásicos, e'))
            doc.append(NoEscape(r'\[ I_{\text{nominal}} = \frac{P}{V \cdot \cos(\phi)} \]'))
            doc.append(NoEscape(r'para circuitos monofásicos, onde \(P\) é a potência, \(V\) a tensão e \(\cos(\phi)\) o fator de potência.'))
        
        with doc.create(Subsection('Cálculo da Corrente Corrigida')):
            doc.append(NoEscape(r'A corrente corrigida (\(I_{\text{corrigida}}\)) considera os fatores de temperatura e agrupamento:'))
            doc.append(NoEscape(r'\[ I_{\text{corrigida}} = \frac{I_{\text{nominal}}}{\text{Fator\_Temperatura} \times \text{Fator\_Agrupamento}} \]'))
        
        with doc.create(Subsection('Seleção do Condutor')):
            doc.append('A seleção do condutor é realizada garantindo que sua capacidade de corrente seja maior que \( I_{\text{corrigida}} \). A seção mínima é determinada pelo método de instalação e as especificações da norma ABNT NBR 5410.')
        
        with doc.create(Subsection('Cálculo da Queda de Tensão')):
            doc.append('A queda de tensão é calculada considerando a resistência e a reatância do condutor, bem como a distância do circuito:')
            doc.append(NoEscape(r'\[ \Delta V =  {I_{\text{nominal}} \times \text{comprimento} \times \text{V/A.km} \]'))
        
        with doc.create(Subsection('Escolha do Disjuntor')):
            doc.append(NoEscape(r'O disjuntor é selecionado assegurando que \( I_{\text{corrigida}} < \) I_{\text{disjuntor}} \( < \) Capacidade de corrente do condutor.'))

    tabela_latex = formatar_tabela_latex(circuitos, disjuntores_gerais, disjuntor_qgbt)
    doc.append(NoEscape(tabela_latex))
    memcal = memcalc(circuitos, resultados, data_tables['queda de tensão'])
    doc.append(NoEscape(memcal))
    # Salvar o arquivo .tex
    doc.generate_tex(caminho_salvar)

def compile_tex_online(tex_content):
    url = "https://latexonline.cc/compile"
    params = {
        "text": tex_content,  # Enviando o conteúdo do arquivo .tex
        "command": "pdflatex"
    }
    response = requests.post(url, params=params)
    if response.status_code == 200:
        return response.content  # Retornando o conteúdo do PDF gerado
    else:
        return None

def adicionar_unidades(df):
    df['potencia'] = df['potencia'].astype(str) + ' W'
    df['Seção do Condutor (mm²)'] = df['Seção do Condutor (mm²)'].astype(str) + ' mm2'
    df['Disjuntor (Ampere)'] = df['Disjuntor (Ampere)'].astype(str) + ' A'
    return df

def ordenar_por_nome(df):
    if 'nome' not in df.columns:
        raise ValueError("A coluna 'nome' não está presente no DataFrame.")
    return df.sort_values(by='nome').reset_index(drop=True)

doc = ezdxf.new(dxfversion='R2010')
msp = doc.modelspace()
def gerar_diagrama_unifilar(exemplos_circuitos,disjuntores_gerais):
    # Agrupa os circuitos pelo quadro
    if not isinstance(exemplos_circuitos, pd.DataFrame):
        exemplos_circuitos = pd.DataFrame(exemplos_circuitos)
    quadros = exemplos_circuitos.groupby('Quadro')
    
    x_offset = 0
    y_offset = 0
    y_offset_last=50
    for nome_quadro, df_quadro in quadros:
        # Adiciona um bloco para o quadro
        
        y_offset -= 50  # Espaçamento entre o quadro e seus circuitos

        df_unifilar = pd.DataFrame({
            'num_fases': [circuito['num_fases'] for circuito in df_quadro.to_dict('records')],
            'nome': [circuito['nome'] for circuito in df_quadro.to_dict('records')],
            'potencia': [f"{circuito['potencia']} W" for circuito in df_quadro.to_dict('records')],
            'Seção do Condutor (mm²)': [f"{circuito['Seção do Condutor (mm²)']} mm2" for circuito in df_quadro.to_dict('records')],
            'Disjuntor (Ampere)': [f"{circuito['Disjuntor (Ampere)']} A" for circuito in df_quadro.to_dict('records')],
            'Fases': [circuito['Fases'] for circuito in df_quadro.to_dict('records')]
        })
        df_ordenado_unifilar = ordenar_por_nome(df_unifilar)

        quadro_min_x = float('inf')
        quadro_min_y = float('inf')
        quadro_max_x = float('-inf')
        quadro_max_y = float('-inf')
        num_circuitos = len(df_ordenado_unifilar)
        circuito_central_index = num_circuitos // 2
        for index, row in df_ordenado_unifilar.iterrows():
            if row['num_fases'] == 1:
                disjuntor_filename = 'Disjuntor_mono.dxf'
                disjuntor_block_name = 'Disjuntor_Mono'
                fios_filename = 'fios_mono.dxf'
                fios_block_name = 'Fios_Mono'
            elif row['num_fases'] == 2:
                disjuntor_filename = 'Disjuntor_bi.dxf'
                disjuntor_block_name = 'Disjuntor_Bi'
                fios_filename = 'fios_bi.dxf'
                fios_block_name = 'Fios_Bi'
            elif row['num_fases'] == 3:
                disjuntor_filename = 'Disjuntor_tri.dxf'
                disjuntor_block_name = 'Disjuntor_Tri'
                fios_filename = 'fios_tri.dxf'
                fios_block_name = 'Fios_Tri'
            disjuntor_attributes = {'corrente': str(row['Disjuntor (Ampere)'])}
            fios_attributes = {
                'seção': str(row['Seção do Condutor (mm²)']),
                'Potência': str(row['potencia']),
                'nome': row['nome'],
                'fases': row['Fases']
            }
            insert_point_disjuntor = (x_offset, y_offset)
            insert_dxf_block_with_attributes(msp, disjuntor_filename, disjuntor_block_name, insert_point_disjuntor, disjuntor_attributes)
            insert_point_fios = (x_offset + 70, y_offset + 30)
            insert_dxf_block_with_attributes(msp, fios_filename, fios_block_name, insert_point_fios, fios_attributes)

            if index == circuito_central_index:
                entrada_tri_attributes = {
                    'CORRENTE': str(disjuntores_gerais[nome_quadro])
                }
                insert_point_entrada_tri = (x_offset, y_offset)
                insert_dxf_block_with_attributes(msp, 'entrada_tri.dxf', 'entrada', insert_point_entrada_tri, entrada_tri_attributes)



            y_offset -= 30
            

            quadro_min_x = -70
            quadro_min_y = y_offset
            quadro_max_x = 90
            quadro_max_y = y_offset_last-30
    
            
        y_offset_last=y_offset-30
        # Adiciona o retângulo em torno do quadro
        padding = 10
        msp.add_text(nome_quadro, dxfattribs={'height': 10}).set_placement((quadro_min_x-padding, quadro_max_y + 20), align=TextEntityAlignment.TOP_LEFT)
        msp.add_lwpolyline([
            (quadro_min_x - padding, quadro_max_y + padding),
            (quadro_max_x + padding, quadro_max_y + padding),
            (quadro_max_x + padding, quadro_min_y - padding),
            (quadro_min_x - padding, quadro_min_y - padding),
            (quadro_min_x - padding, quadro_max_y + padding)
        ], close=True)

        y_offset -= 70  # Espaçamento entre diferentes quadros

    output_path = 'diagrama_unifilar_ajustado.dxf'
    doc.saveas(output_path)
    
    return output_path

def insert_dxf_block_with_attributes(msp, block_filename, block_name, insert_point, attributes):
    try:
        block_doc = ezdxf.readfile(block_filename)
        if block_name not in block_doc.blocks:
            raise ValueError(f"Block {block_name} not found in the file {block_filename}")
        block = block_doc.blocks.get(block_name)
        if block_name not in doc.blocks:
            new_block = doc.blocks.new(name=block_name)
            for entity in block:
                new_block.add_entity(entity.copy())
        block_ref = msp.add_blockref(block_name, insert_point)
        for tag, value in attributes.items():
            block_ref.add_attrib(tag, value)
    except Exception as e:
        print(f"Error inserting block {block_name} from {block_filename}: {e}")

def reordenar_colunas(df):
    ordem_colunas = ['Potência', 'tensão', 'fator_potencia', 'num_fases', 'temperatura', 'num_circuitos', 'comprimento', 'queda_tensao_max_admitida', 'Quadro', 'met_instala']
    return df.reindex(columns=ordem_colunas)


sample_data = [
    {
        "nome": "Circuito 1",
        "potencia": 1000,
        "tensao": 220,
        "fator_potencia": 0.95,
        "num_fases": 1,
        "temperatura": 30,
        "num_circuitos": 2,
        "comprimento": 0.1,
        "met_instala": "3 condutores carregados – método B1 ( Amperes)",
        "Quadro": "Q1"
    },
    {
        "nome": "Circuito 2",
        "potencia": 1500,
        "tensao": 127,
        "fator_potencia": 0.9,
        "num_fases": 1,
        "temperatura": 35,
        "num_circuitos": 3,
        "comprimento": 0.2,
        "met_instala": "3 condutores carregados – método B1 ( Amperes)",
        "Quadro": "Q1"
    },
    {
        "nome": "Circuito 3",
        "potencia": 2000,
        "tensao": 220,
        "fator_potencia": 0.85,
        "num_fases": 1,
        "temperatura": 25,
        "num_circuitos": 1,
        "comprimento": 0.15,
        "met_instala": "3 condutores carregados – método B1 ( Amperes)",
        "Quadro": "Q1"
    }
]

# Interface do Streamlit
st.title('Calculadora de circuitos Elétricos de Baixa Tensão - NBR 5410')
with st.expander(("Sobre a Calculadora")):
    st.markdown((
        """
    A Calculadora de Circuitos Elétricos é uma ferramenta para engenheiros e técnicos que trabalham no setor elétrico, especialmente aqueles focados em instalações de baixa tensão. Esta calculadora é desenvolvida com base na norma brasileira NBR 5410, garantindo que todos os cálculos e projetos estejam em conformidade com as regulamentações e padrões de segurança vigentes.

    ## Funcionalidades Principais

    ### 1. Exportação de Diagrama Unifilar
    A calculadora gera diagramas unifilares precisos e detalhados. Estes diagramas são fundamentais para a visualização clara dos circuitos elétricos, mostrando as conexões entre os componentes e facilitando a interpretação e execução do projeto.

    ### 2. Memória de Cálculo em LaTeX
    Uma das funcionalidades mais avançadas é a capacidade de exportar a memória de cálculo em LaTeX. Isso proporciona um documento profissional e bem formatado, que pode ser facilmente integrado a relatórios técnicos e documentos oficiais.

    ### 3. Resolução de Circuitos
    A calculadora resolve automaticamente os circuitos elétricos, levando em consideração todos os parâmetros necessários, como correntes, tensões e potências. Isso assegura a precisão e eficiência no dimensionamento dos circuitos.

    ### 4. Lista de Materiais
    Ao final do processo, a ferramenta fornece uma lista completa de materiais necessários para a execução do projeto. Esta lista inclui todos os componentes, suas especificações e quantidades, facilitando a aquisição e logística dos materiais.

    """
    ))

st.header('Etapa Inicial')
st.markdown("""
Preencha a planilha de Circuitos. Você pode copiar e colar os dados diretamente de uma planilha Excel, se preferir. 
""")
file_path = 'sample_circuitos.xls'

# Provide download link for the existing Excel file
data_sheets = pd.read_excel('Dados para o gpt.xls', sheet_name=None)
uploaded_file_dados = {sheet_name: data_sheets[sheet_name] for sheet_name in data_sheets}

methods = [
    "2 condutores carregados – método B1 ( Amperes)",
    "3 condutores carregados – método B1 ( Amperes)",
    "2 condutores carregados – método B2 ( Amperes)",
    "3 condutores carregados – método B2 ( Amperes)",
    "2 condutores carregados – método C ( Amperes)",
    "3 condutores carregados – método C (Amperes)"
]
temp = [10, 15, 20, 25, 35, 40, 45]
config = {
    "nome": st.column_config.TextColumn("Nome do Circuito", width="large", required=True),
    "potencia": st.column_config.NumberColumn("Potência Nominal"),
    "tensao": st.column_config.NumberColumn("Tensão Nominal"),
    "fator_potencia": st.column_config.NumberColumn("Fator de Potência"),
    "num_fases": st.column_config.NumberColumn("Número de Fases"),
    "temperatura": st.column_config.SelectboxColumn("Temperatura", options=temp),
    "num_circuitos": st.column_config.NumberColumn("Número de Circuitos Agrupados"),
    "comprimento": st.column_config.NumberColumn("Comprimento (km)"),
    "met_instala": st.column_config.SelectboxColumn("Método de Instalação", options=methods),
    "Quadro": st.column_config.TextColumn("Nome do Quadro", required=True)
}



uploaded_file_circuitos = st.data_editor(sample_data, column_config=config, num_rows="dynamic")

# Aplicar renomeação

print(uploaded_file_circuitos)

st.subheader("Configuração de Alimentação")
tipo_alimentacao = st.selectbox(
    "Selecione o tipo de alimentação geral:",
    ("Trifásica", "Monofásica", "Bifásica")
)
if tipo_alimentacao == "Trifásica":
    fases_QD = 3
    # Adicione sua lógica para trifásico aqui
elif tipo_alimentacao == "Monofásica":
    fases_QD = 1
    # Adicione sua lógica para monofásico aqui
elif tipo_alimentacao == "Bifásica":
    fases_QD = 2
st.sidebar.header("Sobre o Autor")
st.sidebar.markdown("""
Este aplicativo foi desenvolvido por [Matheus Vianna](https://matheusvianna.com). Engenheiro Eletricista com especialização em Ciência de Dados. Confira meu site clicando no meu nome!
""")
st.sidebar.header("__Doação__")
st.sidebar.markdown("""
Se você gostou do programa e quer apoiar o desenvolvimento deste e de outros aplicativos, considere fazer uma doação:
- **Pix:** matheusviannapr@gmail.com
""")





seção_neutro_map = {
    25: 25,
    35: 35,
    50: 35,
    70: 50,
    95: 50,
    120: 70,
    150: 70,
    185: 95,
    240: 120,
    300: 150,
    400: 185
}

seção_terra_map = {
    25: 16,
    35: 16,
    50: 25,
    70: 35,
    95: 50,
    120: 70,
    150: 95,
    185: 95,
    240: 120,
    300: 150
    }
    
if uploaded_file_dados and st.button('Calcular Parâmetros'):
    data_tables = uploaded_file_dados
    if data_tables is not None:
        exemplos_circuitos = uploaded_file_circuitos
        #exemplos_circuitos = reordenar_colunas(exemplos_circuitos)

        if exemplos_circuitos is not None:
            exemplos_circuitos=distribuir_fases(exemplos_circuitos,fases_QD)
            for circuito in exemplos_circuitos:
                circuito['queda_tensao_max_admitida'] = 0.05 * circuito['tensao']
            resultados_circuitos, exemplos_circuitos = calcular_parametros_circuitos(exemplos_circuitos, data_tables)
            st.subheader('Resultados dos Circuitos')
            st.write(resultados_circuitos)
            output = BytesIO()
            resultados_circuitos.to_excel(output, index=False)
            output.seek(0)
            st.download_button(label="Baixar Resultados", data=output, file_name='resultados_circuitos.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            st.subheader('Tabela de Materiais')
            df_selecionado = resultados_circuitos[['Nome do Circuito', 'Seção do Condutor (mm²)', 'Disjuntor','Comprimento','Número de fases']]
            df_selecionado['Quantidade de condutor fase'] = df_selecionado['Comprimento'] * df_selecionado['Número de fases']*1000
            # Adicionar coluna para "Seção do Condutor Neutro" com regra de s <= 25
            df_selecionado['Seção do Condutor Neutro (mm²)'] = df_selecionado['Seção do Condutor (mm²)'].apply(
                lambda x: x if x <= 25 else seção_neutro_map.get(x, x)
            )

            # Adicionar coluna para "comprimento neutro"
            df_selecionado['Comprimento neutro'] = df_selecionado.apply(
                lambda row: row['Comprimento'] * 1000 if row['Número de fases'] == 1 else 0,
                axis=1
            )

            # Adicionar coluna para "Seção do Condutor de Terra" com regra de s <= 16
            df_selecionado['Seção do Condutor de Terra (mm²)'] = df_selecionado['Seção do Condutor (mm²)'].apply(
                lambda x: x if x <= 16 else seção_terra_map.get(x, x)
            )
            # Adicionar coluna para "comprimento terra"
            df_selecionado['Comprimento terra'] = df_selecionado['Comprimento']*1000
            st.write(df_selecionado)
            disjuntoresgerais=calcular_disjuntor_geral(exemplos_circuitos,data_tables['FatordeDemanda'],127)

            disjQGBT=calcular_disjuntor_qgbt(disjuntoresgerais,data_tables['FatordeDemanda'],127)
            output_path = gerar_diagrama_unifilar(exemplos_circuitos,disjuntoresgerais)
            st.success(f"Diagrama salvo em {output_path}")
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(label="Baixar Diagrama Unifilar", data=open(output_path, "rb").read(), file_name='diagrama_unifilar_ajustado.dxf')
            caminho_arquivo = 'memcalc'  # Caminho completo do arquivo latex ser gerado
            criar_relatorio_latex(exemplos_circuitos, resultados_circuitos, caminho_arquivo,disjuntoresgerais,disjQGBT,data_tables)
            with col2:
                st.download_button(label="Baixar Memorial de Cálculo", data=open('memcalc.tex', "rb").read(), file_name='memcalc.tex')
            with st.expander(("Como abrir o Diagrama Unifilar")):
                st.markdown((
                    """
                Para atualizar os parâmetros dos blocos no seu unifilar utilizando o AutoCAD, siga estas instruções:

                1.Abra o arquivo do unifilar no AutoCAD.

                2.Digite o comando 'battman' na linha de comando do AutoCAD e pressione Enter.

                3.A janela "Block Attribute Manager" será aberta. Nela, você deverá atualizar todos os blocos para que o atributo apareça no local correto.

                4.Faça as alterações desejadas e clique em "OK" para aplicar as mudanças.

                """
                ))
            with st.expander(("Como abrir o Memorial de Cálculo")):
                st.markdown((
                    """
                    Trata-se de um código em Latex, portanto é necesária sua compilação. Se você não possui um compilador, sugiro a plataforma OverLeaf.

                    1.Acesse o site do Overleaf (https://www.overleaf.com/).

                    2.Faça login ou crie uma conta, se ainda não tiver uma.

                    3.Após o login, clique em "New Project" e selecione "Upload Project".

                    4.Selecione o arquivo do memorial de cálculo em LaTeX que você deseja abrir e faça o upload.

                    5.Após o upload, o projeto será aberto no editor do Overleaf, onde você poderá visualizar e editar o documento em LaTeX.
                """
                ))
            
else:
    st.warning('Por favor, faça o upload dos arquivos necessários para calcular os parâmetros dos circuitos.')
