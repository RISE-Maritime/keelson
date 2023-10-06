// use bytes::BufMut;

// use prost_types::Timestamp;

use prost::Message;

mod core {
    include!(concat!(env!("OUT_DIR"), "/core.rs"));
}

pub fn enclose(payload: &Vec<u8>) -> Vec<u8> {
    let mut env = core::Envelope::default();

    env.payload = payload.to_vec();
    env.encode_to_vec()
}

pub fn add(left: usize, right: usize) -> usize {
    left + right
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_works() {
        let result = add(2, 2);
        assert_eq!(result, 4);
    }
}
