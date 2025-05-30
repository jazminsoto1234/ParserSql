import os
import struct
import hashlib
import json
from datetime import date, datetime
from .Record import RecordGeneric
import psycopg2


class BinStorageManager:

    def __init__(self, bin_dir='bin_data/Tablas/', pg_conn=None):

        self.meta = None
        self.bin_dir = bin_dir
        self.meta_file = os.path.join(bin_dir, "meta.json")
        os.makedirs(bin_dir, exist_ok=True)
        self._load_metadata()
        self.pg_conn = pg_conn  # conexión a PostgreSQL obligatoria si no se pasa header

    def _load_metadata(self):
        if os.path.exists(self.meta_file):
            with open(self.meta_file, 'r') as f:
                self.meta = json.load(f)
        else:
            self.meta = {}

    def add_index_to_attribute(self, table_name, attribute_name, index_type):

        table_name = table_name.lower()
        attribute_name = attribute_name.lower()
        index_type = index_type.lower()

        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                self.meta = json.load(f)

            if table_name not in self.meta:
                raise ValueError(f"La tabla '{table_name}' no existe en metadata.")

            updated = False
            for column in self.meta[table_name]["columns"]:
                if column["name"].lower() == attribute_name:

                    # por siaca, porque lo hacemos en un inicio
                    if "indexes" not in column:
                        column["indexes"] = []

                    if index_type not in column["indexes"]:
                        column["indexes"].append(index_type)
                        updated = True
                    break
            else:
                raise ValueError(f"El atributo '{attribute_name}' no existe en la tabla '{table_name}'.")

            if updated:

                # Actualiza el timestamp de modificación
                self.meta[table_name]["last_modified"] = datetime.now().isoformat()

                with open(self.meta_file, "w", encoding="utf-8") as f:
                    json.dump(self.meta, f, indent=4)

            else:
                print(f"El índice '{index_type}' ya existe para el atributo '{attribute_name}'.")

        except Exception as e:
            print(f"Error al agregar índice: {e}")

    def get_type_att(self, table_name, attribute_name):

        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

            table_name = table_name.lower()
            attribute_name = attribute_name.lower()

            if table_name not in meta:
                raise ValueError(f"La tabla '{table_name}' no se encuentra en el archivo meta.json.")

            for column in meta[table_name]["columns"]:
                if column["name"] == attribute_name:
                    return column["type"]

            raise ValueError(f"El atributo '{attribute_name}' no se encuentra en la tabla '{table_name}'.")

        except Exception as e:
            print(f"Error al cargar el tipo del atributo: {e}")
            return None

    def get_last_row_by_attribute(self, table_name, attribute_name):

        if not self.pg_conn:
            raise ValueError("Conexión PostgreSQL no proporcionada.")

        try:
            cursor = self.pg_conn.cursor()

            # Preparar consulta segura con placeholders
            query = f"""
                SELECT * FROM {table_name}
                ORDER BY {attribute_name} DESC
                LIMIT 1;
            """
            cursor.execute(query)
            row = cursor.fetchone()
            colnames = [desc[0] for desc in cursor.description]

            cursor.close()

            if row:
                return dict(zip(colnames, row))
            else:
                return None

        except Exception as e:
            print(f"Error al obtener la última fila de la tabla '{table_name}': {e}")
            return None

    def get_atts_table(self, table_name):

        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

            table_name = table_name.lower()

            if table_name not in meta:
                raise ValueError(f"La tabla '{table_name}' no se encuentra en el archivo meta.json.")

            atts = [column["name"] for column in meta[table_name]["columns"]]

            return atts

        except Exception as e:
            print(f"Error al cargar el tipo del atributo: {e}")
            return None

    def get_indexs_att(self, table_name, attribute_name):
        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

            table_name = table_name.lower()
            attribute_name = attribute_name.lower()

            if table_name not in meta:
                raise ValueError(f"La tabla '{table_name}' no se encuentra en el archivo meta.json.")

            for column in meta[table_name]["columns"]:
                if column["name"] == attribute_name:
                    return column.get("indexes", [])

            raise ValueError(f"El atributo '{attribute_name}' no se encuentra en la tabla '{table_name}'.")

        except Exception as e:
            print(f"Error al obtener los índices del atributo: {e}")
            return []

    def _save_metadata(self):
        with open(self.meta_file, 'w') as f:
            json.dump(self.meta, f, indent=4)

    def _get_table_path(self, table_name):
        return os.path.join(self.bin_dir, f"{table_name.lower()}.bin")

    def _compute_hash(self, rows):
        def _default_serializer(obj):
            if isinstance(obj, (date, datetime)):
                return obj.isoformat()
            raise TypeError(f"Object of type {obj._class.name_} is not JSON serializable")
        row_str = json.dumps(rows, sort_keys=True, default=_default_serializer).encode('utf-8')
        return hashlib.md5(row_str).hexdigest()

    def _get_index_postgres(self, table_name):

        if not self.pg_conn:
            raise ValueError("No se puede reconstruir header: conexión PostgreSQL no proporcionada")

        cursor = self.pg_conn.cursor()

        # Obtener columnas que son clave primaria
        cursor.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary;
        """, (table_name.lower(),))
        pk_columns = [row[0] for row in cursor.fetchall()]


        # Obtener columnas y tipos
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
        """, (table_name.lower(),))
        columns = cursor.fetchall()

        # Obtener índices
        cursor.execute("""
            SELECT a.attname AS column_name, am.amname AS index_type
            FROM pg_class t
            JOIN pg_index ix ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_am am ON i.relam = am.oid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE t.relname = %s;
        """, (table_name.lower(),))

        index_info = cursor.fetchall()


        index_map = {}
        for col, idx_type in index_info:
            index_map.setdefault(col, []).append(idx_type.lower())

        cursor.close()

        return columns, index_map, pk_columns

    def _reconstruct_header_from_postgres(self, table_name):
        if not self.pg_conn:
            raise ValueError("No se puede reconstruir header: conexión PostgreSQL no proporcionada")

        columns, index_map, pk_columns = self._get_index_postgres(table_name)

        def sql_to_token(col_name, sql_type):
            cursor = self.pg_conn.cursor()
            cursor.execute(f"""
                SELECT pg_get_expr(adbin, adrelid)
                FROM pg_attrdef
                JOIN pg_class ON pg_class.oid = pg_attrdef.adrelid
                WHERE pg_class.relname = %s AND adnum = (
                    SELECT attnum FROM pg_attribute
                    WHERE attrelid = pg_class.oid AND attname = %s
                )
            """, (table_name.lower(), col_name.lower()))

            result = cursor.fetchone()
            cursor.close()

            if result and result[0] and "nextval" in result[0].lower():
                return "SERIAL"

            return {
                'integer': 'INT',
                'double precision': 'FLOAT',
                'boolean': 'BOOLEAN',
                'character varying': 'TEXT',
                'text': 'TEXT',
                'date': 'DATE',
                'ARRAY': 'ARRAY',
                'smallint': 'INT',
                'real': 'FLOAT'
            }.get(sql_type.lower(), 'TEXT')

        header = []
        for col_name, sql_type in columns:
            header.append({
                "name": col_name.lower(),
                "type": sql_to_token(col_name, sql_type),
                "indexes": index_map.get(col_name, []),
                "primary_key": col_name in pk_columns
            })

        return header

    def save_table(self, table_name, rows, header=None):
        file_path = self._get_table_path(table_name)

        # Forzar siempre guardar si se pasa un nuevo header explícito
        if os.path.exists(file_path) and self.is_synced(table_name, rows) and header is None:
            print(f"[BIN] No se modificó '{table_name}.bin', está sincronizado.")
            return

        with open(file_path, 'wb') as f:
            # Determinar encabezado y metainformación
            if not header:
                # Recuperar de meta.json si ya fue guardado
                meta = self.meta.get(table_name.lower())
                if meta and "columns" in meta:
                    header_names = [col["name"] for col in meta["columns"]]
                    types_info = meta["columns"]
                else:
                    # Reconstruir desde PostgreSQL
                    header = self._reconstruct_header_from_postgres(table_name)
                    header_names = [col["name"] for col in header]
                    types_info = header
            else:
                # Header explícito (desde Python)
                if isinstance(header[0], dict) and "name" in header[0]:
                    header_names = [col["name"] for col in header]
                    types_info = header
                else:
                    raise ValueError("Header debe ser una lista de diccionarios con 'name', 'type', 'indexes'")

            # Guardar encabezado binario
            encoded_header = [s.encode('utf-8') for s in header_names]
            f.write(struct.pack('I', len(encoded_header)))
            for col in encoded_header:
                f.write(struct.pack('I', len(col)))
                f.write(col)

            # Guardar filas
            for row in rows:
                values = [str(row.get(col, '')).encode('utf-8') for col in header_names]
                total_len = sum(4 + len(val) for val in values)
                f.write(struct.pack('I', total_len))
                for val in values:
                    f.write(struct.pack('I', len(val)))
                    f.write(val)

        # Actualizar metadatos
        self.meta[table_name.lower()] = {
            "hash": self._compute_hash(rows),
            "columns": types_info,
            "last_modified": datetime.now().isoformat()
        }
        self._save_metadata()

    def load_table(self, table_name):
        file_path = self._get_table_path(table_name)
        meta = self.meta.get(table_name.lower(), {})
        types = {}
        if meta and "columns" in meta:
            types = {col["name"]: col["type"].upper() for col in meta["columns"]}

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"No existe archivo binario para '{table_name}'")

        rows = []
        with open(file_path, 'rb') as f:
            num_cols = struct.unpack('I', f.read(4))[0]
            header = []
            for _ in range(num_cols):
                col_len = struct.unpack('I', f.read(4))[0]
                col_name = f.read(col_len).decode('utf-8')
                header.append(col_name)

            while True:
                len_bytes = f.read(4)
                if not len_bytes:
                    break
                total_len = struct.unpack('I', len_bytes)[0]
                end = f.tell() + total_len
                values = []
                while f.tell() < end:
                    val_len = struct.unpack('I', f.read(4))[0]
                    val = f.read(val_len).decode('utf-8')
                    values.append(val)

                typed_row = {}
                for col_name, val in zip(header, values):
                    col_type = types.get(col_name, "TEXT")

                    try:
                        if col_type in ["INT", "SERIAL"]:
                            typed_row[col_name] = int(val)
                        elif col_type == "FLOAT":
                            typed_row[col_name] = float(val)
                        elif col_type == "BOOLEAN":
                            typed_row[col_name] = val.lower() in ['true', '1']
                        elif col_type == "DATE":
                            typed_row[col_name] = datetime.fromisoformat(val).date()
                        elif col_type.startswith("ARRAY"):
                            typed_row[col_name] = json.loads(val)
                        else:
                            typed_row[col_name] = val
                    except Exception:
                        typed_row[col_name] = val  # fallback si falla la conversión

                rows.append(typed_row)

        return rows

    def is_synced(self, table_name, rows):
        current_hash = self._compute_hash(rows)
        saved_info = self.meta.get(table_name.lower(), {})
        return saved_info.get("hash") == current_hash

    def load_records_as_objects(self, table_name):
        """
        Carga los registros del archivo binario como objetos RecordGeneric
        """
        rows = self.load_table(table_name)
        if not rows:
            return []

        attribute_names = list(rows[0].keys())

        record_objects = []
        for row in rows:
            record = RecordGeneric(attribute_names)
            for attr in attribute_names:
                setattr(record, attr, row.get(attr, None))
            record_objects.append(record)

        return record_objects