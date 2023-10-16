import pathlib
from .jpeg_structure import JpegStructure, JpegSegment
from .segment_utils import *

class JpegParser():
    def __init__(self) -> None:
        self.jpeg_bytes = None
        self.jpeg_structure = None

    def get_jpeg_bytes(self) -> bytes:
        return self.jpeg_bytes

    def set_jpeg_bytes(self, jpeg_bytes: bytes) -> bool:
        self.jpeg_bytes = jpeg_bytes
        return jpeg_bytes != None and len(self.jpeg_bytes) != 0

    def read_jpeg_file(self, jpeg_file: pathlib.Path) -> bool:
        if not jpeg_file.exists():
            return False
        
        with open(jpeg_file, "rb") as file_handle:
            return self.set_jpeg_bytes(file_handle.read())

    def parse(self, investigation_info) -> bool:
        if self.jpeg_bytes == None:
            return False

        parsing_done = False
        parsing_search_offset = 0
        self.jpeg_structure = JpegStructure()

        while not parsing_done:
            #search for next 0xff appearance
            ff_position = self.jpeg_bytes.find(b"\xff", parsing_search_offset)

            #in case nothing was found, done
            if ff_position == -1:
                parsing_done = True
                continue

            segment_id_position = ff_position + 1

            #check if segment_id_position is in bounds
            if segment_id_position >= len(self.jpeg_bytes):
                parsing_done = True
                continue

            segment_id = self.jpeg_bytes[segment_id_position]
            new_segment = JpegSegment(segment_id, ff_position)

            if segment_id == 0 or segment_id == 255:
                # \xff\x00 and \xff\xff
                if len(investigation_info.characteristics["null_segments"]) == 0:
                    investigation_info.set_integrity_error("null_segments", f"Encountered null segments due to parsing errors!")
                investigation_info.add_characteristic("null_segments", ff_position)
                parsing_search_offset = ff_position + 2
                continue
            if segment_id == 216:
                # \xff\xd8 - Magic Number
                self.jpeg_structure.add_segment(new_segment)

                investigation_info.set_integrity_error("magic_number_not_found", None)

                if not ff_position == 0:
                    investigation_info.set_integrity_error("magic_number_misplaced", f"Magic Number could not be verified: Found on pos {ff_position}, but expected at 0!")

                parsing_search_offset = ff_position + 2
                continue
            if segment_id == 217:
                # \xff\xd9 - End of Image
                self.jpeg_structure.add_segment(new_segment)
                
                parsing_search_offset = ff_position + 2
                continue

            payload_position = ff_position + 2
            new_segment.set_payload_length(calculate_default_payload_length(self.jpeg_bytes, payload_position))
            default_length_info = True #determines if segment header is 4 (default) or 2 bytes in total

            if segment_id == 196:
                # \xff\xc4 - Huffman Table
                investigation_info.add_characteristic("huffman_tables", ff_position)
            if segment_id == 218:
                # \xff\xda - Start of Scan
                default_length_info = False

                eoi_position = self.jpeg_bytes.find(b"\xff\xd9", ff_position + 2)
                if eoi_position == -1:
                    investigation_info.set_stego_attribute("jsteg", "eoi_marker_not_found", True)
                    investigation_info.set_integrity_error("eoi_marker_not_found", f"Could not find EOI segment after SOS marker!")
                    
                    new_segment.set_payload_length(len(self.jpeg_bytes) - payload_position)
                else:
                    new_segment.set_payload_length(eoi_position - payload_position)
            elif segment_id == 219:
                # \xff\xdb - Quantization Table
                investigation_info.add_characteristic("quantization_tables", ff_position)

                payload_length = new_segment.get_payload_length()
                payload_data = extract_payload_data(self.jpeg_bytes, ff_position, payload_length)

                #f5 signature
                if ff_position == 20 and payload_length == 132:
                    if payload_data[82:132] == b"(" * 50:
                        investigation_info.set_stego_attribute("f5", "suspicious_quantization_table", True)

                #outguess signature
                if ff_position == 89 and payload_length == 67:
                    if payload_data[17:67] == b"2" * 50:
                        investigation_info.set_stego_attribute("outguess", "suspicious_quantization_table", True)
            elif segment_id == 224:
                # \xff\xe0 - JFIF-Tag
                investigation_info.set_stego_attribute("jsteg", "jfif_marker_not_found", False)
            elif segment_id == 225:
                # \xff\xe1 - Application 1
                if check_exif_signature(self.jpeg_bytes, payload_position):
                    investigation_info.add_characteristic("exif_data", ff_position)
                if check_xmp_profile_signature(self.jpeg_bytes, payload_position):
                    XMP_END = b"<?xpacket end=\"w\"?>"
                    xmp_end_position = self.jpeg_bytes.find(XMP_END, payload_position)
                    if not xmp_end_position == -1:
                        new_segment.set_payload_length(xmp_end_position + len(XMP_END) - payload_position)
                        investigation_info.add_characteristic("xmp_profiles", ff_position)
            elif segment_id == 226:
                # \xff\xe2 - Application 2
                if check_icc_profile_signature(self.jpeg_bytes, payload_position):
                    investigation_info.add_characteristic("icc_profiles", ff_position)

                    icc_profile_length_info = self.jpeg_bytes[payload_position + 14:payload_position + 16]
                    if icc_profile_length_info[0] < icc_profile_length_info[1]:
                        new_segment.set_payload_length(65535)
            elif segment_id == 237:
                # \xff\xed - Application 13
                if check_photoshop_signature(self.jpeg_bytes, payload_position):
                    investigation_info.add_characteristic("photoshop_data", ff_position)
                    
                    ps_signature_position = payload_position + 16
                    ps_signature = self.jpeg_bytes[ps_signature_position:ps_signature_position + 4].decode(errors = "ignore")

            new_segment.set_segment_data(
                extract_segment_header(self.jpeg_bytes, ff_position, default_length_info),
                extract_payload_data(self.jpeg_bytes, ff_position, new_segment.get_payload_length())
            )

            self.jpeg_structure.add_segment(new_segment)
            parsing_search_offset = ff_position + 2 + new_segment.get_payload_length()

        investigation_info.set_segment_info(self.jpeg_structure.get_segments())
        return True
